output "vpc_id" {
  description = "ID of the VPC"
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the two public subnets (one per AZ)"
  value       = module.vpc.public_subnets
}

output "control_plane_public_ip" {
  description = "Public IP address of the Kubernetes control plane"
  value       = module.k8s_cluster.control_plane_public_ip
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer in front of the Ingress Controller"
  value       = module.k8s_cluster.alb_dns_name
}

output "ingress_fqdn" {
  description = "Public hostname (prod) that resolves to the Ingress Controller via the ALB"
  value       = module.k8s_cluster.ingress_fqdn
}

output "ingress_dev_fqdn" {
  description = "Public hostname (dev) that resolves to the Ingress Controller via the ALB"
  value       = module.k8s_cluster.ingress_dev_fqdn
}

output "alerts_sns_topic_arn" {
  description = "ARN of the SNS topic Alertmanager publishes to; paste into infra/k8s/monitoring/values.yaml's sns_configs.topic_arn"
  value       = module.k8s_cluster.alerts_sns_topic_arn
}
