terraform {
  required_version = ">= 1.10"

  # Partial configuration: the bucket name contains the account id, so it is
  # passed at init time (-backend-config="bucket=...") from TF_STATE_BUCKET.
  backend "s3" {
    key          = "envs/prod/terraform.tfstate"
    region       = "eu-west-2"
    encrypt      = true
    use_lockfile = true
  }

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
      project     = "mind-the-data"
      environment = "prod"
      managed_by  = "terraform"
    }
  }
}
