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

variable "worker_instance_type" {
  description = "EC2 instance type for worker nodes"
  type        = string
  default     = "t3.medium"
}

variable "worker_min_size" {
  description = "Minimum number of worker nodes in the Auto Scaling Group"
  type        = number
  default     = 1
}

variable "worker_max_size" {
  description = "Maximum number of worker nodes in the Auto Scaling Group"
  type        = number
  default     = 3
}

variable "worker_desired_capacity" {
  description = "Desired number of worker nodes; set to 0 when the cluster is not in use to avoid cost"
  type        = number
  default     = 1
}

variable "ssm_join_command_path" {
  description = "SSM parameter path where the current kubeadm join command is stored"
  type        = string
  default     = "/rina-polyai-k8s/join-command"
}
