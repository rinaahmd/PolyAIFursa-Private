variable "vpc_id" {
  description = "ID of the VPC the cluster resources will be placed in"
  type        = string
}

variable "subnet_ids" {
  description = "IDs of the public subnets available to the cluster (control plane and workers)"
  type        = list(string)
}

variable "env" {
  description = "Environment label used for tagging cluster resources"
  type        = string
  default     = "shared"
}
