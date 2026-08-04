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

variable "env" {
  description = "Environment label used for tagging cluster resources"
  type        = string
  default     = "shared"
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

variable "region" {
  description = "AWS region, needed for SSM API calls inside user_data scripts"
  type        = string
}

variable "ssm_join_command_path" {
  description = "SSM parameter path where the current kubeadm join command is stored"
  type        = string
  default     = "/rina-polyai-k8s/join-command"
}
