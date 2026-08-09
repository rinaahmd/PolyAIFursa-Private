output "control_plane_public_ip" {
  description = "Public IP address of the Kubernetes control plane"
  value       = aws_instance.control_plane.public_ip
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer in front of the Ingress Controller"
  value       = aws_lb.ingress.dns_name
}

output "ingress_fqdn" {
  description = "Public hostname (prod) that resolves to the Ingress Controller via the ALB"
  value       = aws_route53_record.ingress_app.fqdn
}

output "ingress_dev_fqdn" {
  description = "Public hostname (dev) that resolves to the Ingress Controller via the ALB"
  value       = aws_route53_record.ingress_dev.fqdn
}

output "alerts_sns_topic_arn" {
  description = "ARN of the SNS topic Alertmanager publishes to; paste into infra/k8s/monitoring/values.yaml's sns_configs.topic_arn"
  value       = aws_sns_topic.alerts.arn
}
