#!/usr/bin/env bash
# Build the temporary layer (network + EKS + nodes) and point kubectl at it.
#
#   ./infra/up.sh
#
# Costs about $0.25/hour while the cluster exists. Run ./infra/down.sh when done.
set -euo pipefail
cd "$(dirname "$0")/cluster"

PROFILE=patrol
EXPECTED_ACCOUNT=419701112481
CLUSTER=patrol
REGION=us-east-1
started=$(date +%s)

account=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
if [[ "$account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol ($EXPECTED_ACCOUNT)." >&2
  exit 1
fi

for tool in terraform kubectl aws curl; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool (try: brew install $tool)" >&2; exit 1; }
done

# The cluster API only accepts connections from your current public IP.
ip=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
if [[ ! "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Could not determine your public IPv4 address (got: '$ip')." >&2
  exit 1
fi
echo "==> Cluster API will accept connections from ${ip}/32 only"

AWS_PROFILE=$PROFILE terraform init -input=false
AWS_PROFILE=$PROFILE terraform apply -var "admin_cidrs=[\"${ip}/32\"]"

echo "==> Configuring kubectl (context: $CLUSTER)"
aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" --profile "$PROFILE" --alias "$CLUSTER"

echo "==> Waiting for nodes to be Ready"
kubectl --context "$CLUSTER" wait --for=condition=Ready nodes --all --timeout=10m
kubectl --context "$CLUSTER" get nodes -o wide

elapsed=$(( $(date +%s) - started ))
printf '==> Cluster up in %dm %02ds\n' $((elapsed / 60)) $((elapsed % 60))
