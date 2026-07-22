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

resource "aws_instance" "polyai_dev" {
  ami           = "ami-0b6d9d3d33ba97d99"
  instance_type = "t2.nano"
  key_name      = aws_key_pair.polyai_dev_key.key_name


  vpc_security_group_ids = [aws_security_group.polyai_dev_sg.id]

  tags = {
    Name      = "rina-polyai-dev"
    Env       = "dev"
    Terraform = "true"
    Project   = "PolyAI"
  }
}

resource "aws_security_group" "polyai_dev_sg" {
  name        = "rina-polyai-dev-sg"
  description = "Allow SSH and HTTP traffic"

  ingress {
    description = "Allow SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "rina-polyai-dev-sg"
    Env  = "dev"
  }
}

resource "aws_key_pair" "polyai_dev_key" {
  key_name   = "rina-polyai-dev-key"
  public_key = file(pathexpand("~/.ssh/rina-polyai-dev.pub"))

  tags = {
    Name = "rina-polyai-dev-key"
    Env  = "dev"
  }
}