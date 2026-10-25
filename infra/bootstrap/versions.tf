# Bootstrap layer for Patrol.
#
# This is the one config that keeps its state LOCALLY, because it creates the
# S3 bucket every other layer stores its state in (chicken-and-egg).
# Run it once, by hand, with the "patrol" profile. Never from CI.

terraform {
  required_version = ">= 1.10.0" # 1.10+ needed later for S3-native locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = "patrol"
      Layer     = "bootstrap"
      ManagedBy = "terraform"
    }
  }
}
