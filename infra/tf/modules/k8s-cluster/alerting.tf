resource "aws_sns_topic" "alerts" {
  name = "rina-polyai-k8s-alerts"

  tags = {
    Name  = "rina-polyai-k8s-alerts"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_iam_role_policy" "worker_sns_publish" {
  name = "rina-polyai-k8s-worker-sns-publish"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "sns:Publish"
      Resource = aws_sns_topic.alerts.arn
    }]
  })
}
