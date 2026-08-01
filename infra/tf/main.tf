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
  region  = var.region
  profile = "default"
}

data "aws_availability_zones" "available" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.8.1"

  name = "polyai-k8s-vpc"
  cidr = var.vpc_cidr

  azs            = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets = var.public_subnet_cidrs

  map_public_ip_on_launch = true
  enable_nat_gateway      = false

  tags = {
    Env       = var.env
    Project   = "PolyAI"
    Terraform = "true"
  }
}

module "k8s_cluster" {
  source = "./modules/k8s-cluster"

  vpc_id              = module.vpc.vpc_id
  vpc_cidr            = var.vpc_cidr
  subnet_ids          = module.vpc.public_subnets
  ssh_public_key_path = var.ssh_public_key_path
  env                 = var.env
}