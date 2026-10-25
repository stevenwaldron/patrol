variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "azs" {
  type = list(string)
}

variable "public_subnet_cidrs" {
  type = list(string)
}

variable "private_subnet_cidrs" {
  type = list(string)
}

variable "database_subnet_cidrs" {
  description = "Database subnets; pass [] for none (no database tier or route table is created)."
  type        = list(string)
  default     = []
}

variable "single_nat_gateway" {
  type    = bool
  default = false
}

variable "public_subnet_tags" {
  description = "Extra tags for public subnets (e.g. kubernetes.io/role/elb = 1 for EKS internet-facing load balancers)."
  type        = map(string)
  default     = {}
}

variable "private_subnet_tags" {
  description = "Extra tags for private subnets (e.g. kubernetes.io/role/internal-elb = 1 for EKS internal load balancers)."
  type        = map(string)
  default     = {}
}
