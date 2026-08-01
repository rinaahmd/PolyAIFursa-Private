output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the two public subnets (one per AZ)"
  value       = module.vpc.public_subnets
}
