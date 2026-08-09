resource "aws_security_group" "alb" {
  name        = "rina-polyai-k8s-alb-sg"
  description = "ALB: HTTPS from the internet, egress to worker NodePorts"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from the internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name  = "rina-polyai-k8s-alb-sg"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_lb" "ingress" {
  name               = "rina-polyai-k8s-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.subnet_ids

  tags = {
    Name  = "rina-polyai-k8s-alb"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_lb_target_group" "ingress_http" {
  name        = "rina-polyai-k8s-ingress-tg"
  port        = var.ingress_http_node_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    path                = "/healthz"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  tags = {
    Name  = "rina-polyai-k8s-ingress-tg"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.ingress.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.ingress.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ingress_http.arn
  }
}

resource "aws_autoscaling_attachment" "ingress" {
  autoscaling_group_name = aws_autoscaling_group.worker.name
  lb_target_group_arn    = aws_lb_target_group.ingress_http.arn
}

data "aws_route53_zone" "shared" {
  name         = var.route53_zone_name
  private_zone = false
}

resource "aws_acm_certificate" "ingress" {
  domain_name               = var.dns_record_name
  subject_alternative_names = [var.dev_dns_record_name]
  validation_method         = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name  = "rina-polyai-k8s-ingress-cert"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_route53_record" "ingress_cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.ingress.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id         = data.aws_route53_zone.shared.zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "ingress" {
  certificate_arn         = aws_acm_certificate.ingress.arn
  validation_record_fqdns = [for r in aws_route53_record.ingress_cert_validation : r.fqdn]
}

resource "aws_route53_record" "ingress_app" {
  zone_id = data.aws_route53_zone.shared.zone_id
  name    = var.dns_record_name
  type    = "A"

  alias {
    name                   = aws_lb.ingress.dns_name
    zone_id                = aws_lb.ingress.zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "ingress_dev" {
  zone_id = data.aws_route53_zone.shared.zone_id
  name    = var.dev_dns_record_name
  type    = "A"

  alias {
    name                   = aws_lb.ingress.dns_name
    zone_id                = aws_lb.ingress.zone_id
    evaluate_target_health = true
  }
}
