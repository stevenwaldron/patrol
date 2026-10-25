variable "region" {
  type    = string
  default = "us-east-1"
}

variable "expected_account_id" {
  description = "Safety check: plan/apply fails if credentials point at any other account."
  type        = string
  default     = "419701112481"
}

variable "cluster_name" {
  type    = string
  default = "patrol"
}

variable "kubernetes_version" {
  description = "One behind the newest EKS version: add-on and Helm chart support for a brand-new minor version usually lags by weeks."
  type        = string
  default     = "1.36"
}

variable "admin_cidrs" {
  description = "Public IPs allowed to reach the cluster API (your Mac's current IP as /32). Set by infra/up.sh."
  type        = list(string)

  validation {
    condition     = length(var.admin_cidrs) > 0 && alltrue([for c in var.admin_cidrs : can(cidrnetmask(c)) && c != "0.0.0.0/0"])
    error_message = "admin_cidrs must be valid IPv4 CIDRs, and not 0.0.0.0/0."
  }
}

variable "admin_role_arn" {
  description = "IAM role that gets cluster-admin through an EKS access entry (the role your CLI profile assumes)."
  type        = string
  default     = "arn:aws:iam::419701112481:role/OrganizationAccountAccessRole"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.medium"]
}

variable "node_min_size" {
  type    = number
  default = 2
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_max_size" {
  type    = number
  default = 3
}

# Network layout: 10.20.0.0/16
#   public  /24 x2  -> load balancers and the NAT gateway only
#   private /20 x2  -> nodes and pods (~4,000 addresses each; prefix delegation
#                      hands out /28 blocks, and small subnets fragment)
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "azs" {
  type    = list(string)
  default = ["us-east-1a", "us-east-1b"]
}

variable "public_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.0.0/24", "10.20.1.0/24"]
}

variable "private_subnet_cidrs" {
  type    = list(string)
  default = ["10.20.16.0/20", "10.20.32.0/20"]
}
