# Permanent layer for Patrol.
#
# Everything here stays up between work sessions and costs almost nothing:
# container registries, the job queue, the results table and the Slack secret.
# The temporary layer (network + EKS) is built and destroyed around it.
#
# Run every command with the profile set inline, e.g.
#   AWS_PROFILE=patrol terraform plan
# (inline, so it can't leak into other commands; no profile is hardcoded,
#  so the same config works later from GitHub Actions)

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "patrol-tfstate-419701112481"
    key          = "permanent/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true # S3-native locking; no DynamoDB lock table needed
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "patrol"
      Layer     = "permanent"
      ManagedBy = "terraform"
    }
  }
}
