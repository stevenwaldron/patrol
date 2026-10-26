#!/usr/bin/env bash
# Destroy the temporary layer. The permanent layer (DynamoDB, SQS, ECR, the
# Slack secret, the state bucket) is untouched.
#
#   ./infra/down.sh
#
# Order matters. Load balancers created BY KUBERNETES (from an Ingress or a
# LoadBalancer Service) are unknown to Terraform. If the cluster is destroyed
# first, they're orphaned: still billing, and blocking the VPC from deleting.
# So: delete them through Kubernetes, wait until AWS confirms they're gone,
# then run terraform destroy, then confirm nothing was left behind.
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

# Load balancers the AWS Load Balancer Controller made for this cluster carry this tag.
k8s_load_balancers() {
  aws resourcegroupstaggingapi get-resources --profile "$PROFILE" --region "$REGION" \
    --resource-type-filters elasticloadbalancing:loadbalancer \
    --tag-filters "Key=elbv2.k8s.aws/cluster,Values=$CLUSTER" \
    --query 'ResourceTagMappingList[].ResourceARN' --output text
}

if kubectl --context "$CLUSTER" get namespaces >/dev/null 2>&1; then
  # Phase 3+: Argo CD would recreate anything deleted below, so its apps go first.
  if kubectl --context "$CLUSTER" get crd applications.argoproj.io >/dev/null 2>&1; then
    echo "==> Deleting Argo CD applications (so they can't recreate load balancers)"
    kubectl --context "$CLUSTER" delete applications.argoproj.io --all -A --wait=true --timeout=5m || true
  fi
  echo "==> Deleting Ingresses and LoadBalancer Services"
  kubectl --context "$CLUSTER" delete ingress --all -A --wait=true --timeout=5m || true
  kubectl --context "$CLUSTER" get svc -A \
    -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}' |
    while read -r ns name; do
      if [[ -n "$ns" ]]; then
        kubectl --context "$CLUSTER" delete svc -n "$ns" "$name" --wait=true --timeout=5m
      fi
    done
else
  echo "==> Cluster API not reachable (already gone, or your IP changed). Skipping Kubernetes cleanup."
fi

echo "==> Waiting for AWS to finish deleting Kubernetes-created load balancers"
for _ in $(seq 1 30); do
  [[ -z "$(k8s_load_balancers)" ]] && break
  sleep 10
done
if [[ -n "$(k8s_load_balancers)" ]]; then
  echo "Load balancers still exist; terraform destroy would fail on the VPC:" >&2
  k8s_load_balancers >&2
  echo "Delete them (EC2 console > Load Balancers), then rerun this script." >&2
  exit 1
fi

# The variable is required, but its value doesn't matter for a destroy.
ip=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]' || echo 203.0.113.1)
AWS_PROFILE=$PROFILE terraform init -input=false
AWS_PROFILE=$PROFILE terraform destroy -var "admin_cidrs=[\"${ip}/32\"]"

echo "==> Checking for leftovers (asking each service directly)"
# Not the tagging index: it lags deletions by minutes and reported a deleted NAT
# gateway and subnets as "left over" on the first real run. These calls are
# authoritative. They cover everything that could keep billing or block a rebuild.
leftovers=0
check() {  # check "description" "output of an AWS query"
  if [[ -n "$2" && "$2" != "False" && "$2" != "None" ]]; then
    echo "  LEFTOVER  $1: $2"
    leftovers=$((leftovers + 1))
  else
    echo "  ok        $1"
  fi
}
check "EKS cluster" "$(aws eks list-clusters --profile "$PROFILE" --region "$REGION" \
  --query "contains(clusters, '$CLUSTER')" --output text)"
check "Node EC2 instances" "$(aws ec2 describe-instances --profile "$PROFILE" --region "$REGION" \
  --filters "Name=tag:eks:cluster-name,Values=$CLUSTER" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
check "NAT gateways" "$(aws ec2 describe-nat-gateways --profile "$PROFILE" --region "$REGION" \
  --filter "Name=tag:Project,Values=patrol" "Name=state,Values=pending,available,deleting,failed" \
  --query 'NatGateways[].NatGatewayId' --output text)"
check "Elastic IPs" "$(aws ec2 describe-addresses --profile "$PROFILE" --region "$REGION" \
  --filters "Name=tag:Project,Values=patrol" --query 'Addresses[].PublicIp' --output text)"
check "VPC" "$(aws ec2 describe-vpcs --profile "$PROFILE" --region "$REGION" \
  --filters "Name=tag:Project,Values=patrol" "Name=tag:Layer,Values=cluster" --query 'Vpcs[].VpcId' --output text)"
check "Kubernetes load balancers" "$(k8s_load_balancers)"
if (( leftovers > 0 )); then
  echo "==> $leftovers leftover type(s) found. They may still be billing: check the console." >&2
else
  echo "==> Nothing left over."
fi

kubectl config delete-context "$CLUSTER" >/dev/null 2>&1 || true
elapsed=$(( $(date +%s) - started ))
printf '==> Cluster down in %dm %02ds\n' $((elapsed / 60)) $((elapsed % 60))
