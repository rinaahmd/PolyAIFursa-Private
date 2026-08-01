#!/bin/bash
set -euxo pipefail

# --- Kernel prerequisites for Kubernetes networking ---
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
CRIO_VERSION=1.30
curl -fsSL https://pkgs.k8s.io/addons:/cri-o:/prerelease:/main/deb/Release.key |
  gpg --dearmor -o /etc/apt/keyrings/cri-o-apt-keyring.gpg
echo "deb [signed-by=/etc/apt/keyrings/cri-o-apt-keyring.gpg] https://pkgs.k8s.io/addons:/cri-o:/prerelease:/main/deb/ /" |
  tee /etc/apt/sources.list.d/cri-o.list
apt-get update -y
apt-get install -y cri-o
systemctl enable --now crio

# --- Install kubelet, kubeadm, kubectl ---
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

# --- Initialize the control plane ---
kubeadm init --pod-network-cidr=${pod_network_cidr}

# --- Configure kubeconfig for the ubuntu user ---
mkdir -p /home/ubuntu/.kube
cp -i /etc/kubernetes/admin.conf /home/ubuntu/.kube/config
chown ubuntu:ubuntu /home/ubuntu/.kube/config

# --- Also configure kubeconfig for root, for convenience during automation (Phase 6) ---
mkdir -p /root/.kube
cp -i /etc/kubernetes/admin.conf /root/.kube/config
