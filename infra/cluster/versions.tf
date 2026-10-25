# Temporary layer for Patrol: network + EKS cluster + nodes.
#
# Built at the start of a work session and destroyed at the end (infra/up.sh,
# infra/down.sh). Nothing here holds data: results live in DynamoDB, images in
# ECR, secrets in Secrets Manager (all in the permanent layer).
#
# Run with the profile inline, e.g.  AWS_PROFILE=patrol terraform plan ...

terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.59" # the EKS module v21.29 needs >= 6.59
    }
  }

  backend "s3" {
    bucket       = "patrol-tfstate-419701112481"
    key          = "cluster/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "patrol"
      Layer     = "cluster"
      ManagedBy = "terraform"
    }
  }
}
