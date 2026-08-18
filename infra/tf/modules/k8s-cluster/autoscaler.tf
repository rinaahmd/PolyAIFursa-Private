data "aws_caller_identity" "current" {}

# Cluster Autoscaler runs as a Pod on a worker node and authenticates via
# the worker instance profile over IMDS (same credential path Alertmanager
# already uses for SNS - see alerting.tf). Read-only Describe/Get calls
# can't be scoped to a specific resource in AWS's IAM model, so those stay
# on "*"; the two mutating calls that actually change capacity are scoped
# to this cluster's own worker ASG only.
resource "aws_iam_role_policy" "worker_cluster_autoscaler" {
  name = "rina-polyai-k8s-worker-cluster-autoscaler"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ClusterAutoscalerDiscovery"
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:DescribeAutoScalingInstances",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:DescribeScalingActivities",
          "autoscaling:DescribeTags",
          "ec2:DescribeImages",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeLaunchTemplateVersions",
          "ec2:GetInstanceTypesFromInstanceRequirements",
        ]
        Resource = "*"
      },
      {
        Sid    = "ClusterAutoscalerScaling"
        Effect = "Allow"
        Action = [
          "autoscaling:SetDesiredCapacity",
          "autoscaling:TerminateInstanceInAutoScalingGroup",
        ]
        Resource = "arn:aws:autoscaling:${var.region}:${data.aws_caller_identity.current.account_id}:autoScalingGroup:*:autoScalingGroupName/${aws_autoscaling_group.worker.name}"
      },
    ]
  })
}
