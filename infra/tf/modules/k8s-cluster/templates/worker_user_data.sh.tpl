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
