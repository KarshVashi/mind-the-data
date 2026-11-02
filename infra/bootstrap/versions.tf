terraform {
  required_version = ">= 1.10"

  # Local state on purpose: this root creates the remote state bucket.
  # Keep terraform.tfstate safe after applying (see docs/runbook.md).

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69.1"
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
