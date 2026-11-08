terraform {
  # use_lockfile (S3-native locking) is generally available from 1.11.
  required_version = ">= 1.11"

  # State lives in the bucket this root creates, under bootstrap/, which CI
  # can read (plan role, for the drift check) but never write. Partial
  # configuration: the bucket name carries the account id, so it is passed at
  # init time. scripts/bootstrap.sh does that; see docs/runbook.md (H6) for
  # the one-time move from local state, and for rebuilding from nothing.
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.70.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      project    = "mind-the-data"
      managed_by = "terraform"
      stack      = "bootstrap"
    }
  }
}
