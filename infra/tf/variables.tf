variable "env" {
  description = "Deployment environment label used for tagging (the cluster itself hosts both dev and prod via namespaces)"
  type        = string
  default     = "shared"
}

variable "region" {
  description = "AWS region to provision the cluster in"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the cluster VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "CIDR blocks for the two public subnets (one per Availability Zone)"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}
