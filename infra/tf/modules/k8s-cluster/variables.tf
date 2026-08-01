variable "vpc_id" {
  description = "ID of the VPC the cluster resources will be placed in"
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block of the VPC, used to scope intra-VPC security group rules"
  type        = string
}

variable "subnet_ids" {
  description = "IDs of the public subnets available to the cluster (control plane and workers)"
  type        = list(string)
}

variable "ssh_public_key_path" {
  description = "Path to the local public key file used to create the cluster's AWS key pair"
  type        = string
}

variable "env" {
  description = "Environment label used for tagging cluster resources"
  type        = string
  default     = "shared"
}
