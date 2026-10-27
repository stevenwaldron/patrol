#!/usr/bin/env bash
# Build the temporary layer (network + EKS + nodes), point kubectl at it, then
# install Argo CD and hand it the root app: from there, Git installs everything
# else (load balancer controller, then Patrol). Ends when the status page answers.
#
#   ./infra/up.sh
#
# Costs about $0.25/hour while the cluster exists. Run ./infra/down.sh when done.
set -euo pipefail
cd "$(dirname "$0")/cluster"
REPO_ROOT=$(cd ../.. && pwd)

PROFILE=patrol
EXPECTED_ACCOUNT=419701112481
CLUSTER=patrol
REGION=us-east-1
ARGOCD_CHART_VERSION=10.11.6   # = Argo CD v3.5.6
started=$(date +%s)

account=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
if [[ "$account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol ($EXPECTED_ACCOUNT)." >&2
  exit 1
fi

for tool in terraform kubectl helm aws curl; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
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
nodes_ready=$(date +%s)
printf '==> Cluster up in %dm %02ds\n' $(( (nodes_ready - started) / 60 )) $(( (nodes_ready - started) % 60 ))

# ---------------------------------------------------------------------------
# Argo CD: the only thing installed by hand. Everything after it comes from Git.
# ---------------------------------------------------------------------------
echo "==> Installing Argo CD (Helm chart argo-cd ${ARGOCD_CHART_VERSION})"
helm upgrade --install argocd argo-cd \
  --repo https://argoproj.github.io/argo-helm --version "$ARGOCD_CHART_VERSION" \
  --kube-context "$CLUSTER" --namespace argocd --create-namespace \
  --values "$REPO_ROOT/deploy/argocd/values.yaml" --wait --timeout 10m

echo "==> Handing the cluster to Git (root app reads deploy/argocd/apps)"
kubectl --context "$CLUSTER" apply -f "$REPO_ROOT/deploy/argocd/root-app.yaml"

# Waits until an Argo CD app reports Synced + Healthy, printing each change of state.
wait_for_app() {  # wait_for_app NAME TIMEOUT_SECONDS
  local name=$1 deadline=$(( $(date +%s) + $2 )) state="" last=""
  while (( $(date +%s) < deadline )); do
    state=$(kubectl --context "$CLUSTER" -n argocd get applications.argoproj.io "$name" \
      -o jsonpath='{.status.sync.status}/{.status.health.status}' 2>/dev/null || true)
    if [[ "$state" != "$last" ]]; then
      echo "    $name: ${state:-not created yet}"
      last=$state
    fi
    if [[ "$state" == "Synced/Healthy" ]]; then return 0; fi
    sleep 10
  done
  echo "Timed out waiting for Argo CD app '$name' (last state: ${state:-none})." >&2
  echo "Check: kubectl --context $CLUSTER -n argocd describe application $name" >&2
  return 1
}

echo "==> Waiting for Argo CD: load balancer controller first (wave -1), then Patrol (wave 0)"
wait_for_app aws-load-balancer-controller 600
wait_for_app patrol 900

echo "==> Waiting for the status page to answer through the load balancer"
host=$(kubectl --context "$CLUSTER" -n patrol get ingress status-page \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
up=false
for _ in $(seq 1 60); do   # a new ALB's DNS name can take a few minutes to resolve
  if curl -fsS -o /dev/null --max-time 5 "http://${host}/ready" 2>/dev/null; then
    up=true
    break
  fi
  sleep 10
done
if [[ "$up" == true ]]; then
  echo "==> Status page: http://${host}/"
else
  echo "Status page not answering yet at http://${host}/ (DNS may still be propagating; try again in a few minutes)." >&2
fi

finished=$(date +%s)
printf '==> Argo CD + apps took %dm %02ds after the nodes were Ready\n' \
  $(( (finished - nodes_ready) / 60 )) $(( (finished - nodes_ready) % 60 ))
printf '==> Everything up in %dm %02ds\n' $(( (finished - started) / 60 )) $(( (finished - started) % 60 ))

cat <<EOF

Argo CD dashboard (private: only through your own machine):
  kubectl --context $CLUSTER -n argocd port-forward svc/argocd-server 8080:443
  then open https://localhost:8080  (a self-signed certificate warning is expected)
  User: admin. Password (copy it, never screenshot it):
  kubectl --context $CLUSTER -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
EOF
