# Account guard: a postcondition on the lookup itself fails the PLAN before
# anything is created. (No depends_on on modules: that defers every data source
# inside them to apply time and causes noisy, sometimes phantom, plan diffs.)
data "aws_caller_identity" "current" {
  lifecycle {
    postcondition {
      condition     = self.account_id == var.expected_account_id
      error_message = "Wrong AWS account: credentials point at ${self.account_id}, expected ${var.expected_account_id}. Did you prefix the command with AWS_PROFILE=patrol?"
    }
  }
}

# ---------------------------------------------------------------------------
# Network: Steven's VPC module, extended for EKS (see modules/vpc/main.tf)
# ---------------------------------------------------------------------------
module "vpc" {
  source = "../modules/vpc"

  project_name          = var.cluster_name
  environment           = "lab"
  vpc_cidr              = var.vpc_cidr
  azs                   = var.azs
  public_subnet_cidrs   = var.public_subnet_cidrs
  private_subnet_cidrs  = var.private_subnet_cidrs
  database_subnet_cidrs = []   # Patrol stores results in DynamoDB; no database tier
  single_nat_gateway    = true # one NAT (~$0.045/h) instead of one per zone; fine for a lab

  # How the AWS Load Balancer Controller finds subnets for load balancers.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
    "karpenter.sh/discovery"          = var.cluster_name # used later by Karpenter (Phase 9)
  }
}

# ---------------------------------------------------------------------------
# EKS cluster + managed node group (community module, version pinned)
# ---------------------------------------------------------------------------
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = var.cluster_name
  kubernetes_version = var.kubernetes_version

  # STANDARD: never silently enter extended support (6x the hourly price).
  upgrade_policy = {
    support_type = "STANDARD"
  }

  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnet_ids # nodes: private only
  control_plane_subnet_ids = module.vpc.private_subnet_ids

  # API endpoint: reachable from your Mac's IP only, plus privately from inside the VPC.
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.admin_cidrs
  endpoint_private_access      = true

  # Access entries only (no aws-auth ConfigMap), and an explicit admin instead of
  # "whoever ran terraform", so a future CI apply can't become cluster admin.
  authentication_mode                      = "API"
  enable_cluster_creator_admin_permissions = false
  access_entries = {
    admin = {
      principal_arn = var.admin_role_arn
      policy_associations = {
        cluster_admin = {
          policy_arn   = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  # Pod Identity instead of IRSA, so no per-cluster OIDC provider is created.
  enable_irsa = false

  # EKS envelope-encrypts all Kubernetes API data by default (AWS-owned key, since
  # March 2025). A customer-managed key would add $1/month and a new key per rebuild.
  create_kms_key    = false
  encryption_config = null

  # Control-plane logs: audit (who did what) + authenticator (who logged in).
  # "api" is left off: very chatty, and CloudWatch bills per GB ingested.
  enabled_log_types                      = ["audit", "authenticator"]
  cloudwatch_log_group_retention_in_days = 7

  addons = {
    # Prefix delegation: nodes get /28 blocks (16 IPs) instead of single IPs,
    # raising the t3.medium pod limit from 17 to 110. Must exist BEFORE the nodes,
    # or they join with the old limit; hence before_compute.
    vpc-cni = {
      before_compute = true
      configuration_values = jsonencode({
        env = {
          ENABLE_PREFIX_DELEGATION = "true"
          WARM_PREFIX_TARGET       = "1"
        }
      })
    }
    # Hands pods their short-lived AWS credentials (Pod Identity). Also before nodes.
    eks-pod-identity-agent = {
      before_compute = true
    }
    coredns    = {}
    kube-proxy = {}
  }

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      ami_type       = "AL2023_x86_64_STANDARD" # x86 to match the images built on the Intel Mac
      capacity_type  = "ON_DEMAND"

      min_size     = var.node_min_size
      desired_size = var.node_desired_size
      max_size     = var.node_max_size

      labels = {
        "patrol.io/pool" = "default"
      }
      # Module defaults kept on purpose: IMDSv2 required, hop limit 1, so pods
      # can't borrow the node's own AWS credentials.
    }
  }
}
