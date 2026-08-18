#!/bin/bash
set -euxo pipefail

# --- Kernel prerequisites for Kubernetes networking (same as control plane) ---
cat <<EOF | tee /etc/modules-load.d/k8s.conf
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter

cat <<EOF | tee /etc/sysctl.d/k8s.conf
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system

swapoff -a
sed -i '/ swap / s/^/#/' /etc/fstab

# --- Install CRI-O (container runtime) ---
curl -fsSL https://pkgs.k8s.io/addons:/cri-o:/prerelease:/main/deb/Release.key |
  gpg --dearmor -o /etc/apt/keyrings/cri-o-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/cri-o-apt-keyring.gpg] https://pkgs.k8s.io/addons:/cri-o:/prerelease:/main/deb/ /" |
  tee /etc/apt/sources.list.d/cri-o.list
apt-get update -y
apt-get install -y cri-o
systemctl enable --now crio

# --- Install kubelet, kubeadm, kubectl (no kubeadm init on workers) ---
K8S_VERSION=1.30
mkdir -p /etc/apt/keyrings
curl -fsSL https://pkgs.k8s.io/core:/stable:/v$${K8S_VERSION}/deb/Release.key |
  gpg --dearmor -o /etc/apt/keyrings/kubernetes-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes-apt-keyring.gpg] https://pkgs.k8s.io/core:/stable:/v$${K8S_VERSION}/deb/ /" |
  tee /etc/apt/sources.list.d/kubernetes.list
apt-get update -y
apt-get install -y kubelet kubeadm kubectl
apt-mark hold kubelet kubeadm kubectl

# --- Set kubelet's --provider-id (self-managed cluster, no AWS Cloud
# Controller Manager) ---
# Cluster Autoscaler's AWS provider maps Kubernetes Nodes back to EC2
# instances/ASGs via Node.spec.providerID. Without a CCM nothing sets that
# field automatically, so kubelet must be started with --provider-id
# itself - format is aws:///<az>/<instance-id>, per
# https://github.com/kubernetes/autoscaler/blob/master/cluster-autoscaler/cloudprovider/aws/README.md
# kubeadm's own systemd drop-in (10-kubeadm.conf) sources
# /etc/default/kubelet as an EnvironmentFile and appends $KUBELET_EXTRA_ARGS
# to the ExecStart line - that's the documented "last resort" override
# path, so this has to be a plain KEY=VALUE file, not a systemd unit.
IMDS_TOKEN=$(curl -fsS -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
AZ=$(curl -fsS -H "X-aws-ec2-metadata-token: $${IMDS_TOKEN}" \
  http://169.254.169.254/latest/meta-data/placement/availability-zone)
INSTANCE_ID=$(curl -fsS -H "X-aws-ec2-metadata-token: $${IMDS_TOKEN}" \
  http://169.254.169.254/latest/meta-data/instance-id)

echo "KUBELET_EXTRA_ARGS=--provider-id=aws:///$${AZ}/$${INSTANCE_ID}" | tee /etc/default/kubelet

systemctl enable --now kubelet

# --- Install AWS CLI v2 (needed for SSM calls below) ---
apt-get install -y unzip
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp
/tmp/aws/install

# --- Fetch the current join command from SSM and join the cluster ---
# Retries handle the race where a worker boots before the control plane's
# token-refresh timer has written a token for the CURRENT cluster (e.g.
# right after the control plane itself was just replaced) -- kubeadm join
# then fails against a stale/unreachable API server. Up to 10 attempts,
# 30s apart (5 min total), matches the control plane's own SSM refresh cadence.
for i in $(seq 1 10); do
  JOIN_CMD=$(aws ssm get-parameter \
    --region ${aws_region} \
    --name "${ssm_join_command_path}" \
    --with-decryption \
    --query 'Parameter.Value' \
    --output text)

  if eval "$JOIN_CMD"; then
    echo "Successfully joined the cluster"
    exit 0
  fi

  echo "kubeadm join failed (attempt $i/10), resetting and retrying in 30s..."
  kubeadm reset -f
  sleep 30
done

echo "Failed to join the cluster after 10 attempts"
exit 1
