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
REPO_ROOT=$(cd ../.. && pwd)

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

wait_for_load_balancers_gone() {
  echo "==> Waiting for AWS to finish deleting Kubernetes-created load balancers"
  for _ in $(seq 1 30); do
    [[ -z "$(k8s_load_balancers)" ]] && return 0
    sleep 10
  done
  echo "Load balancers still exist; terraform destroy would fail on the VPC:" >&2
  k8s_load_balancers >&2
  echo "Delete them (EC2 console > Load Balancers), then rerun this script." >&2
  exit 1
}

argo() { kubectl --context "$CLUSTER" -n argocd "$@"; }

# Patrol's public hostname (empty = no domain, nothing to wait for).
DOMAIN=$(awk '/^  host:/ {gsub(/"/, "", $2); print $2}' "$REPO_ROOT/deploy/charts/patrol/values.yaml")

# ExternalDNS must remove the domain's records BEFORE ExternalDNS itself is
# deleted (same idea as waiting for the ALB). If it doesn't within 3 minutes,
# warn and carry on: leftover records are owned (owner=patrol), so the next
# rebuild's ExternalDNS repoints them; stopping the teardown would cost more.
wait_for_dns_records_gone() {
  [[ -n "$DOMAIN" ]] || return 0
  local zone_id count=""
  zone_id=$(aws route53 list-hosted-zones-by-name --profile "$PROFILE" --dns-name "$DOMAIN" \
    --query "HostedZones[?Name=='${DOMAIN}.'].Id | [0]" --output text)
  if [[ -z "$zone_id" || "$zone_id" == "None" ]]; then return 0; fi
  echo "==> Waiting for ExternalDNS to remove ${DOMAIN}'s DNS records"
  for _ in $(seq 1 18); do
    count=$(aws route53 list-resource-record-sets --profile "$PROFILE" --hosted-zone-id "$zone_id" \
      --query "length(ResourceRecordSets[?Name=='${DOMAIN}.' && (Type=='A' || Type=='AAAA')])" --output text)
    if [[ "$count" == "0" ]]; then
      echo "    removed"
      return 0
    fi
    sleep 10
  done
  echo "WARNING: ${DOMAIN} still has ${count} A/AAAA record(s) after 3 minutes. Continuing the teardown." >&2
  echo "         They are owned by Patrol's ExternalDNS (owner=patrol); the next up.sh repoints them." >&2
}

if kubectl --context "$CLUSTER" get namespaces >/dev/null 2>&1; then
  # Phase 3+ order. The load balancer controller is the only thing that can
  # delete the ALB, so it must still be running when Patrol's Ingress goes.
  # Deleting every Argo CD app at once could remove the controller first and
  # orphan the ALB (still billing, blocking VPC deletion).
  if kubectl --context "$CLUSTER" get crd applications.argoproj.io >/dev/null 2>&1; then
    # 1. Detach the root app WITHOUT cascading (remove its finalizer, then delete
    #    it), so it stops recreating the child apps but doesn't delete them all at once.
    if argo get applications.argoproj.io root >/dev/null 2>&1; then
      echo "==> Detaching the root app (children stay, deleted one by one below)"
      argo patch applications.argoproj.io root --type json \
        -p '[{"op":"remove","path":"/metadata/finalizers"}]' >/dev/null 2>&1 || true
      argo delete applications.argoproj.io root --wait=true --timeout=2m || true
    fi
    # 2. Delete Patrol's app. Its finalizer makes Argo CD delete Patrol's objects,
    #    including the Ingress; the still-running controller then deletes the ALB.
    if argo get applications.argoproj.io patrol >/dev/null 2>&1; then
      echo "==> Deleting the Patrol app (the controller removes its ALB)"
      argo delete applications.argoproj.io patrol --wait=true --timeout=5m || true
    fi
  fi

  # 3. Safety net, still while the controller runs: any other Ingress or
  #    LoadBalancer Service (for example one created by hand while testing).
  echo "==> Deleting any remaining Ingresses and LoadBalancer Services"
  kubectl --context "$CLUSTER" delete ingress --all -A --wait=true --timeout=5m || true
  kubectl --context "$CLUSTER" get svc -A \
    -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}' |
    while read -r ns name; do
      if [[ -n "$ns" ]]; then
        kubectl --context "$CLUSTER" delete svc -n "$ns" "$name" --wait=true --timeout=5m
      fi
    done

  # 4. Only once AWS confirms the load balancers are gone AND ExternalDNS has
  #    removed the domain's records, remove the rest (controller, ExternalDNS).
  wait_for_load_balancers_gone
  wait_for_dns_records_gone
  if kubectl --context "$CLUSTER" get crd applications.argoproj.io >/dev/null 2>&1; then
    echo "==> Deleting the remaining Argo CD apps (load balancer controller, ExternalDNS)"
    argo delete applications.argoproj.io --all --wait=true --timeout=5m || true
  fi
else
  echo "==> Cluster API not reachable (already gone, or your IP changed). Skipping Kubernetes cleanup."
fi

# Again (cheap), in case the Kubernetes cleanup was skipped above.
wait_for_load_balancers_gone

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
