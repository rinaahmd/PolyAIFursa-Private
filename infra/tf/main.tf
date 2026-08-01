terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.55"
    }
  }

  required_version = ">= 1.7.0"
}

provider "aws" {
  region  = "us-east-1"
  profile = "default"
}