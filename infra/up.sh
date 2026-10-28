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

for tool in terraform kubectl helm aws curl dig; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done

# The cluster API only accepts connections from your current public IP.
ip=$(curl -fsS https://checkip.amazonaws.com | tr -d '[:space:]')
if [[ ! "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Could not determine your public IPv4 address (got: '$ip')." >&2
  exit 1
fi
echo "==> Cluster API will accept connections from ${ip}/32 only"

# HTTPS needs the ACM certificate ISSUED (it is once the IONOS delegation to
# Route 53 has spread). Without it the controller can't create the HTTPS
# listener, so don't start paying for a cluster that can't serve.
DOMAIN=$(awk '/^  host:/ {gsub(/"/, "", $2); print $2}' "$REPO_ROOT/deploy/charts/patrol/values.yaml")
if [[ -n "$DOMAIN" ]]; then
  cert=$(aws acm list-certificates --profile "$PROFILE" --region "$REGION" \
    --certificate-statuses ISSUED \
    --query "CertificateSummaryList[?DomainName=='${DOMAIN}'].CertificateArn | [0]" --output text)
  if [[ -z "$cert" || "$cert" == "None" ]]; then
    echo "No ISSUED certificate for ${DOMAIN} yet. Check the IONOS name servers, then:" >&2
    echo "  cd infra/permanent && AWS_PROFILE=patrol terraform output certificate_status" >&2
    exit 1
  fi
  echo "==> Certificate for ${DOMAIN} is ISSUED"
fi

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
apps_ready=$(date +%s)

echo "==> Waiting for the load balancer to be active"
lb_host=""
for _ in $(seq 1 30); do
  lb_host=$(kubectl --context "$CLUSTER" -n patrol get ingress status-page \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  if [[ -n "$lb_host" ]]; then break; fi
  sleep 10
done
lb_state=""
for _ in $(seq 1 60); do
  lb_state=$(aws elbv2 describe-load-balancers --profile "$PROFILE" --region "$REGION" \
    --query "LoadBalancers[?DNSName=='${lb_host}'].State.Code | [0]" --output text 2>/dev/null || true)
  if [[ "$lb_state" == "active" ]]; then break; fi
  sleep 10
done
echo "    ${lb_host:-<no address>}: ${lb_state:-unknown}"

# Success is judged by asking AWS's OWN nameserver for the address and connecting
# straight to it. Looking the name up through this machine's normal DNS can hit a
# cached "doesn't exist" from an earlier lookup (that false alarm happened on two
# runs), so the normal-DNS result is shown for information only.
up=false
url=""
if [[ -n "$DOMAIN" ]]; then
  url="https://${DOMAIN}"
  zone_id=$(aws route53 list-hosted-zones-by-name --profile "$PROFILE" --dns-name "$DOMAIN" \
    --query "HostedZones[?Name=='${DOMAIN}.'].Id | [0]" --output text)
  ns=$(aws route53 get-hosted-zone --profile "$PROFILE" --id "$zone_id" \
    --query 'DelegationSet.NameServers[0]' --output text)
  echo "==> Waiting for ExternalDNS to point ${DOMAIN} at it"
  record=""
  for _ in $(seq 1 30); do
    record=$(aws route53 list-resource-record-sets --profile "$PROFILE" --hosted-zone-id "$zone_id" \
      --query "ResourceRecordSets[?Name=='${DOMAIN}.' && Type=='A'].AliasTarget.DNSName | [0]" --output text)
    if [[ "${record%.}" == "$lb_host" ]]; then break; fi
    sleep 10
  done
  echo "    ${DOMAIN} -> ${record}"
  echo "==> Checking HTTPS (address from AWS's nameserver ${ns})"
  for _ in $(seq 1 30); do
    ip=$(dig +short A "$DOMAIN" @"$ns" | grep -E '^[0-9.]+$' | head -1 || true)
    if [[ -n "$ip" ]] && curl -fsS -o /dev/null --max-time 5 \
         --resolve "${DOMAIN}:443:${ip}" "${url}/ready" 2>/dev/null; then
      up=true
      break
    fi
    sleep 10
  done
else
  url="http://${lb_host}"
  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null --max-time 5 "${url}/ready" 2>/dev/null; then
      up=true
      break
    fi
    sleep 10
  done
fi

if [[ "$up" == true ]]; then
  echo "==> Status page: ${url}/"
  if [[ -n "$DOMAIN" ]]; then
    if curl -fsS -o /dev/null --max-time 5 "${url}/ready" 2>/dev/null; then
      echo "    (this machine's DNS sees it too)"
    else
      echo "    (live, but this machine's DNS hasn't caught up yet: an earlier lookup is"
      echo "     cached for up to a minute. Other machines see it now.)"
    fi
  fi
else
  echo "Status page did NOT answer at ${url}/ (load balancer: ${lb_state:-unknown})." >&2
  echo "This is not a DNS cache issue: check the Ingress and pods:" >&2
  echo "  kubectl --context $CLUSTER -n patrol describe ingress status-page" >&2
fi

finished=$(date +%s)
printf '==> Argo CD + apps healthy %dm %02ds after the nodes were Ready\n' \
  $(( (apps_ready - nodes_ready) / 60 )) $(( (apps_ready - nodes_ready) % 60 ))
printf '==> Load balancer + DNS + HTTPS check took %dm %02ds more\n' \
  $(( (finished - apps_ready) / 60 )) $(( (finished - apps_ready) % 60 ))
printf '==> Everything up in %dm %02ds\n' $(( (finished - started) / 60 )) $(( (finished - started) % 60 ))

cat <<EOF

Argo CD dashboard (private: only through your own machine):
  kubectl --context $CLUSTER -n argocd port-forward svc/argocd-server 8080:443
  then open https://localhost:8080  (a self-signed certificate warning is expected)
  User: admin. Password (copy it, never screenshot it):
  kubectl --context $CLUSTER -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
EOF
