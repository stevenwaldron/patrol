# ---------------------------------------------------------------------------
# Karpenter (Phase 8): AWS side. Uses the EKS module's own Karpenter submodule,
# same pinned version as the cluster module (same package, no new provider).
#
# Lives in this temporary layer, unlike the other Pod Identity roles (permanent
# layer): almost everything here is tied to this one cluster (node access entry,
# Pod Identity association, interruption queue) and is rebuilt with it.
#
# The controller's IAM policy is the module's, scoped to this cluster's tags.
# Checked against Karpenter 1.15.1's reference policy (its CloudFormation
# template): every action Karpenter needs is included; the only one missing,
# ec2:RebootInstances, is for an alpha feature gate we don't enable.
# ---------------------------------------------------------------------------
module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "21.29.0"

  cluster_name = module.eks.cluster_name

  # Controller identity: EKS Pod Identity for kube-system/karpenter (the chart's
  # defaults, Karpenter's documented install location). Fixed names, so the
  # Git-side config (deploy/platform/karpenter) can refer to them.
  iam_role_name                   = "patrol-karpenter"
  iam_role_use_name_prefix        = false
  create_pod_identity_association = true
  namespace                       = "kube-system"
  service_account                 = "karpenter"

  # Spot interruption handling: AWS sends the 2-minute reclaim warning (and
  # rebalance/health events) to this queue through EventBridge rules; Karpenter
  # reads it and drains the node before it disappears.
  enable_spot_termination = true
  queue_name              = "patrol-karpenter"

  # Node identity. Terraform also creates the instance profile, and the
  # EC2NodeClass names it, so Karpenter never creates instance profiles itself:
  # nothing for teardown to miss (an instance profile left holding the role
  # would block deleting it).
  node_iam_role_name            = "patrol-karpenter-node"
  node_iam_role_use_name_prefix = false
  create_instance_profile       = true
  # Same as the managed nodes: the module attaches the worker, CNI and ECR
  # read-only policies. Plus SSM, for Session Manager access to a node during
  # the failure drills (no SSH keys, no open ports).
  node_iam_role_additional_policies = {
    ssm = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }
}
