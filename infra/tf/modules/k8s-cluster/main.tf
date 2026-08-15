data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_key_pair" "control_plane_key" {
  key_name   = "rina-polyai-k8s-control-plane-key"
  public_key = file("${path.module}/files/control-plane-key.pub")

  tags = {
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_iam_role" "control_plane" {
  name = "rina-polyai-k8s-control-plane-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Env       = var.env
    Project   = "PolyAI"
    Terraform = "true"
    Owner     = "rina"
  }
}

resource "aws_iam_role_policy_attachment" "control_plane_eks_cluster" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role_policy_attachment" "control_plane_ebs_csi" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role_policy_attachment" "control_plane_ecr_readonly" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "control_plane" {
  name = "rina-polyai-k8s-control-plane-profile"
  role = aws_iam_role.control_plane.name
}

resource "aws_iam_role_policy" "control_plane_ssm_write_join_token" {
  name = "rina-polyai-k8s-control-plane-ssm-write"
  role = aws_iam_role.control_plane.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:PutParameter", "ssm:GetParameter"]
      Resource = "arn:aws:ssm:${var.region}:*:parameter${var.ssm_join_command_path}"
    }]
  })
}

resource "aws_security_group" "control_plane" {
  name        = "rina-polyai-k8s-control-plane-sg"
  description = "Control plane: SSH from anywhere, all traffic within the VPC"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "All traffic within the VPC (control plane and workers)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name  = "rina-polyai-k8s-control-plane-sg"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_instance" "control_plane" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "t3.medium"
  subnet_id              = var.subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.control_plane.id]
  iam_instance_profile   = aws_iam_instance_profile.control_plane.name
  key_name               = aws_key_pair.control_plane_key.key_name

  user_data = templatefile("${path.module}/templates/control_plane_user_data.sh.tpl", {
    pod_network_cidr      = "192.168.0.0/16"
    aws_region            = var.region
    ssm_join_command_path = var.ssm_join_command_path
  })
  user_data_replace_on_change = true

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = {
    Name  = "rina-polyai-k8s-control-plane"
    Env   = var.env
    Role  = "control-plane"
    Owner = "rina"
  }
}

resource "aws_iam_role" "worker" {
  name = "rina-polyai-k8s-worker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Env       = var.env
    Project   = "PolyAI"
    Terraform = "true"
    Owner     = "rina"
  }
}

resource "aws_iam_role_policy_attachment" "worker_eks_cluster" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role_policy_attachment" "worker_ebs_csi" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role_policy_attachment" "worker_ecr_readonly" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "worker" {
  name = "rina-polyai-k8s-worker-profile"
  role = aws_iam_role.worker.name
}

resource "aws_iam_role_policy" "worker_ssm_read_join_token" {
  name = "rina-polyai-k8s-worker-ssm-read"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "ssm:GetParameter"
      Resource = "arn:aws:ssm:${var.region}:*:parameter${var.ssm_join_command_path}"
    }]
  })
}

resource "aws_security_group" "worker" {
  name        = "rina-polyai-k8s-worker-sg"
  description = "Workers: SSH from anywhere, all traffic within the VPC"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "All traffic within the VPC (control plane and workers)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [var.vpc_cidr]
  }

  ingress {
    description     = "Ingress Controller HTTP NodePort from the ALB"
    from_port       = var.ingress_http_node_port
    to_port         = var.ingress_http_node_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "Ingress Controller HTTPS NodePort from the ALB"
    from_port       = var.ingress_https_node_port
    to_port         = var.ingress_https_node_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    description = "All outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name  = "rina-polyai-k8s-worker-sg"
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_launch_template" "worker" {
  name_prefix   = "rina-polyai-k8s-worker-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.worker_instance_type
  key_name      = aws_key_pair.control_plane_key.key_name

  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }

  vpc_security_group_ids = [aws_security_group.worker.id]

  # hop_limit=2 (default is 1) so pods can reach IMDS through the extra
  # network hop the CNI (Calico) adds - otherwise only processes on the
  # host's own network namespace can fetch instance-profile credentials,
  # which breaks anything running as a Pod that needs AWS auth (e.g.
  # Alertmanager's sigv4-signed SNS publish).
  metadata_options {
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
    http_tokens                 = "optional"
  }

  user_data = base64encode(templatefile("${path.module}/templates/worker_user_data.sh.tpl", {
    aws_region            = var.region
    ssm_join_command_path = var.ssm_join_command_path
  }))

  block_device_mappings {
    device_name = "/dev/sda1"

    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

  tag_specifications {
    resource_type = "instance"

    tags = {
      Name  = "rina-polyai-k8s-worker"
      Env   = var.env
      Role  = "worker"
      Owner = "rina"
    }
  }

  tags = {
    Env   = var.env
    Owner = "rina"
  }
}

resource "aws_autoscaling_group" "worker" {
  name = "rina-polyai-k8s-worker-asg"
  # Pinned to a single subnet/AZ (matching where the monitoring EBS
  # volumes already live) so a replacement worker can never land in an AZ
  # that can't mount them - EBS volumes are AZ-local, and the ASG
  # otherwise spreads across all of var.subnet_ids with no regard for
  # existing PV placement, causing a "volume node affinity conflict"
  # any time a new instance happens to land in the other AZ.
  vpc_zone_identifier = [var.subnet_ids[1]]
  min_size            = var.worker_min_size
  max_size            = var.worker_max_size
  desired_capacity    = var.worker_desired_capacity

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  tag {
    key                 = "Name"
    value               = "rina-polyai-k8s-worker"
    propagate_at_launch = true
  }

  tag {
    key                 = "Env"
    value               = var.env
    propagate_at_launch = true
  }

  tag {
    key                 = "Owner"
    value               = "rina"
    propagate_at_launch = true
  }
}
