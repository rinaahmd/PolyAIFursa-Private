terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.55"
    }
  }

  required_version = ">= 1.7.0"

  backend "s3" {
    bucket = "rina-polyai-k8s-tfstate-228281126655"
    key    = "cluster.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.region
}

data "aws_availability_zones" "available" {
  state = "available"
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.8.1"

  name = "rina-polyai-k8s-vpc"
  cidr = var.vpc_cidr

  azs            = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnets = var.public_subnet_cidrs

  map_public_ip_on_launch = true
  enable_nat_gateway      = false

  tags = {
    Env       = var.env
    Project   = "PolyAI"
    Terraform = "true"
    Owner     = "rina"
  }
}

module "k8s_cluster" {
  source = "./modules/k8s-cluster"

  vpc_id                  = module.vpc.vpc_id
  vpc_cidr                = var.vpc_cidr
  subnet_ids              = module.vpc.public_subnets
  env                     = var.env
  worker_instance_type    = var.worker_instance_type
  worker_min_size         = var.worker_min_size
  worker_max_size         = var.worker_max_size
  worker_desired_capacity = var.worker_desired_capacity
  region                  = var.region
  ssm_join_command_path   = var.ssm_join_command_path
}