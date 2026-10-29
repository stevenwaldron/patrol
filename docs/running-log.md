# Patrol: running build log

Source notes for the documentation PDF. Four kinds of entry:
**Decision** (with what was rejected and why), **Bug** (symptom first, then cause, then fix),
**Trade-off**, and **Surprise**.

---

## Planning (before any build)

| Decision | Chosen | Rejected, and why |
|---|---|---|
| Platform | EKS | ECS: simpler and cheaper for this app, but Kubernetes is the skill gap in the portfolio |
| Workload | Fresh uptime monitor ("Patrol") | Port chess (WebSocket fan-out redesign), leaderboard (too thin), online shop (reads as a tutorial) |
| Queue | SQS | Redis in the cluster: one more thing to run, and lost at every teardown |
| Results storage | DynamoDB | Database in the cluster: hard to run well, wiped at every teardown |
| Cost control | Two layers (permanent + temporary), rebuilt per session, $30 budget alert | Always-on cluster: about $180/month |
| Nodes | Managed node group now, Karpenter later | Fargate (no node-level monitoring, no Pod Identity), Auto Mode (hides the skills being demonstrated) |
| Node type | Two small x86 nodes | ARM/Graviton: cheaper, but the Intel Mac builds x86 images; revisit with multi-arch builds in CI |
| Repo | One repo | Separate deployment repo: suits many teams, not a solo project |
| Ownership | Terraform owns AWS, Argo CD owns everything inside the cluster | Both tools managing the same objects |
| Image tags | Commit ID, immutable | "latest": can't tell what's running or roll back reliably |
| Rebuild | Argo CD reinstalls everything from Git after each teardown | Re-applying manifests by hand |
| Monitoring | kube-prometheus-stack, SLO + error budget, Grafana private via port-forward | Managed Prometheus/Grafana: the point is to show running it |
| Alerts | Slack | Email |
| Pod AWS access | EKS Pod Identity, one IAM role per service | IRSA: trust policies tied to a per-cluster OIDC provider, broken by every rebuild; node role: every pod gets every permission |
| Secrets | Secrets Manager + External Secrets Operator | Secrets in Git or in Terraform state |
| Image scanning | Trivy in CI, fail on serious findings | None (a stated gap in both earlier CI/CD write-ups) |
| Policy | Kyverno, audit first then enforce | No admission policy |
| Cluster access | EKS access entries; CI gets none | aws-auth ConfigMap (one bad edit locks everyone out) |
| Worker scaling | KEDA on SQS queue depth | CPU-based HPA: checkers wait on the network, so CPU stays low while the queue grows |
| Language | Python | Node.js |

---

## Phase 0: foundation

**Step 1. Account.** Created the Patrol member account (419701112481) with my own CLI tool,
**aws-organizations-toolkit v2** (`org-account-create`), which creates the account, moves it into the
PROJECTS OU and verifies the move in one command behind a typed confirmation. Verified with `org-inspect`.
- Decision: own toolkit, rejected the console or raw CLI (several manual steps, no built-in verification).
- Access confirmed through `OrganizationAccountAccessRole` via a `patrol` CLI profile.

**Step 2. Bootstrap (state bucket + budget).** 8 resources applied.
- Decision: budget defined in Terraform, rejected clicking it in the console (not recorded, not rebuildable).
- Decision: bootstrap keeps local state, run by hand once (the bucket it creates can't hold its own state).
- Decision: S3-native state locking (`use_lockfile`) for later layers, rejected a DynamoDB lock table (unneeded on Terraform 1.10+).
- Decision: built-in account check (precondition on the caller's account ID), rejected relying on remembering the profile (failed once on chess).
- Budget: $30/month, emails at 50/80/100% actual and 100% forecast. A budget warns; it does not stop spending, and billing data lags by hours, so the teardown script is the real protection.
- **Bug.** Symptom: first `terraform init` failed with "no space left on device" while installing the AWS provider; retry succeeded. Cause: disk 98% full (5 GB free), and every Terraform folder downloads its own multi-hundred-MB provider. Fix: shared provider cache in `~/.terraformrc`; freed space to 26 GB.
- **Surprise.** The S3 lifecycle rule took 56 s while everything else took 1–2 s: the provider waits for S3 to confirm the rule has propagated.
- **Surprise.** During cleanup, a "destroy-only" copy of the chess project still held a state file describing *live* resources in the chess account (its bootstrap subfolder had come along in the copy). Caught by counting resources before deleting; proven an outdated duplicate by matching **lineage** (same state) and **serial** (9 vs the live 11).
  Lesson: never delete a folder containing state without checking which account it points at and whether a newer copy exists.

**Step 3. Permanent layer.** ECR ×3, SQS queue + dead-letter queue, DynamoDB results table, Slack secret.
- Decision: ECR tags IMMUTABLE, so a commit-ID tag always means one build. Rejected mutable tags.
- Decision: ECR lifecycle (untagged gone after 1 day, newest 20 kept) to bound storage cost.
- Decision: dead-letter queue after 3 failed deliveries, so one bad site can't jam the workers.
- Decision: queue retention 15 min; patrol orders are issued every minute, so stale ones are useless.
- Decision: DynamoDB key `site_id` + `checked_at`, on-demand billing, TTL on `expires_at`, deletion protection on.
- Decision: the list of sites to monitor lives in Git (Helm values), not in DynamoDB, so adding a site is a reviewed commit.
- Decision: Terraform creates only the empty Slack secret; the value goes in by CLI, so it never appears in code, plans or state. (Lesson carried over from CloudForger's leaked-plan finding.)
- Decision: no AWS profile hardcoded; commands use an inline `AWS_PROFILE=patrol` prefix, so the same config runs from CI later and the profile can't leak into other commands.

**Step 4. Slack alert path.** Workspace "Patrol Ops", channel #patrol-alerts, Slack app "Patrol Alertmanager" with an incoming webhook.
- Verified the Terraform-created secret was empty before storing anything (`VersionIdsToStages: null`), proving no value passed through Terraform.
- Stored the webhook with a hidden prompt (`read -s`), so it never appears on screen or in shell history.
- End-to-end test reads the URL back *from Secrets Manager* and posts to Slack: proves the stored value works, not just the copy on the clipboard.
- **Bug.** Symptom: `put-secret-value` rejected an empty SecretString (length 0). Cause: the shell variable holding the webhook didn't exist in the session where the store command ran. Fix: read, validate (must start with `https://hooks.slack.com/services/`) and store in one command, printing only the length.
- Screenshot rule: never capture the webhook settings page; the redaction script now also blacks out Slack webhook URLs.

**Phase 0 complete:** account, budget, state bucket, registries, queue + DLQ, results table, Slack secret, verified alert path.

---

## Phase 1: the three Python services

Built `app/` (package `patrol`): scheduler, worker, status page, shared config/sites/logging/checker/store,
29 tests, pinned dependencies, `run-local.sh`.

- Decision: one package, three entrypoints, one image per service. Rejected three separate codebases (duplicated config, logging and storage code).
- Decision: FastAPI for the status page, `httpx` for checks, `boto3` for AWS, `prometheus_client` for metrics.
- Decision: GET not HEAD for checks; some servers answer HEAD with 405, a false "down".
- Decision: a down site is a *successful* check with a "down" result; only internal failures (saving) are errors.
- Decision: delete the SQS message only after the result is saved; on save failure leave it, so SQS redelivers and dead-letters after 3 tries.
- Decision: skip orders older than 2 minutes (a stale check reports the past as the present).
- Decision: readiness does not check DynamoDB. Rejected dependency-checking readiness: a DynamoDB blip would mark every copy not-ready at once (dependency hiccup → full outage).
- Decision: no /metrics on the scheduler (a one-second job can't be scraped). Rejected Prometheus Pushgateway (another component to run). Scheduler failure is caught downstream by `patrol_site_last_checked_age_seconds`.
- Decision: site metrics come from the status page (reads DynamoDB), not the workers. With several workers, each worker's "last result" gauge would disagree; DynamoDB is the single source of truth.
- Decision: request-metric labels use route templates and exclude probes, keeping series bounded and the SLO about real visitors.
- Decision: 30-second cache on the 24h summary, so page loads and scrapes don't each query DynamoDB.
- Decision: deliberately broken test site uses the reserved `.invalid` TLD (RFC 2606), guaranteed never to resolve.
- Decision: worker handles SIGTERM (finish batch, exit 0), so Kubernetes pod stops don't drop work.
- Decision: `run-local.sh` exports settings inside the script only and refuses to run against any account but Patrol.
- Verification: 29 tests with moto (fake SQS/DynamoDB) and a fake HTTP layer. Mutation check: deliberately broke "leave message on save failure" and "stale detection"; the tests caught both, so they test real behaviour.
- Verification: end-to-end run of all three processes against a local moto server and a local web server: scheduler exit 0, worker checked up and down sites, served /health and /metrics, shut down cleanly on SIGTERM; status page reported both correctly.
- **Bug (caught in review).** Symptom: rendered page showed "1 ms" latency for the unreachable test site. Cause: latency was recorded as time-to-failure even when no response arrived. Fix: only display latency when the site actually answered; new test covers it.
- **Bug.** Symptom: `pip install` on the Mac could not find boto3 1.43.115, and pip reported version 19.2.3. Cause: `python3` resolved to Python 3.8, and boto3 dropped 3.8 support after 1.37.38 (the code also needs 3.10+). Fix: build the virtual environment from Homebrew Python 3.13 explicitly, matching the container base image; `run-local.sh` now refuses a venv older than 3.11 with a clear message. Result: 29 passed on the Mac (Python 3.13.11).
- **Surprise.** boto3 reads the region from `AWS_DEFAULT_REGION`, not `AWS_REGION`, when none is passed. The services pass the region explicitly, so they're unaffected; only a setup script tripped on it.
- **Live run on the Mac against real AWS:** scheduler issued 3 orders; worker checked CloudForger (up, 304 ms), chess (up, 294 ms) and the broken test site (down, DNS failure); status page showed all three correctly; worker `/metrics` exposed `patrol_checks_total` per site and result.
- **Surprise.** `prometheus_client` emits an extra `*_created` timestamp series for every counter (`patrol_checks_created`), doubling series count for no use here. Fix: `PROMETHEUS_DISABLE_CREATED_SERIES=True` in the run script (and in the container images next).
- **Surprise.** The same DNS failure reads differently by OS: `[Errno -2] Name or service not known` on Linux, `[Errno 8] nodename nor servname provided` on macOS. The check treats both as down; the page shows whatever the OS reported.

### Phase 1, step 2: container images

One `app/Dockerfile` with three final targets (`scheduler`, `worker`, `status-page`), plus `build-images.sh`,
`run-container.sh` and a root `.dockerignore`.

- Decision: one Dockerfile with shared `deps` and `base` stages and three small final stages. Rejected three Dockerfiles (duplicated, drift apart).
- Decision: base image from **ECR Public** (`public.ecr.aws/docker/library/python:3.13-slim`), AWS's mirror of the official image. Rejected Docker Hub: anonymous pull limits, which shared CI runners hit.
- Decision: removed the `# syntax=docker/dockerfile:1` line. It made every build download a parser image *from Docker Hub*, the dependency the base-image choice avoids; no feature here needs it.
- Decision: dependencies installed in their own cached layer (copy `requirements.txt` first), so code edits rebuild in seconds.
- Decision: numeric non-root user `10001`. Kubernetes can only verify `runAsNonRoot` when `USER` is numeric. Code and config stay root-owned, so the app can read but not modify them.
- Decision: `.dockerignore` is an allow-list (only `app/patrol`, `requirements.txt`, `sites.yaml`), so state files, docs or secrets can never slip into an image.
- Decision: no Docker `HEALTHCHECK`; Kubernetes ignores it and uses its own probes.
- Decision: `run-container.sh` passes **short-lived role credentials** (about 1 hour) exported inside a subshell; `~/.aws` is never mounted into a container. Mirrors how Pod Identity will supply credentials.
- Decision: images built for `linux/amd64`, matching the x86 nodes.
- **Bug.** Symptom: `bash -n` passed, but `run-container.sh scheduler` would crash on macOS. Cause: macOS ships bash 3.2, where expanding an empty array under `set -u` is an "unbound variable" error. Fix: `${ports[@]+"${ports[@]}"}`.
- **Verification (in the build workspace).** Docker Hub and ECR Public are both blocked there, so the real `FROM` couldn't be pulled. Built a minimal stand-in base from the workspace's own Python 3.13 and built the **unchanged** Dockerfile via its `PYTHON_IMAGE` argument. All three images built (131 MB each; the 2nd and 3rd reused every cached layer). Checked: runs as uid 10001, app can't write its own code, `/app` holds only the package and `sites.yaml`, `_created` series gone. End to end against a local moto server: scheduler exit 0; worker `/health` ok and exit 0 after `docker stop` (SIGTERM); status page served up/down correctly and exit 0 after SIGTERM.
- **Surprise.** The stand-in exposed how much a "slim" image quietly provides: pydantic's compiled core needed `libgcc_s` and `librt`, which the official slim image ships as base packages. Found by scanning every compiled library's dependencies with `ldd` instead of fixing them one at a time.
- **Surprise.** With no queue configured, the scheduler container failed fast with "Missing required environment variable(s): PATROL_QUEUE_URL": the startup check working as designed.
- **Lesson (tooling).** `pkill -f PATTERN` and `pgrep -f PATTERN` match their *own* command line when the pattern appears in it, so a cleanup command can kill the shell running it. Use a bracket trick (`pgrep -f 'moto_serve[r]'`) so the pattern doesn't match itself.
- **Real build on the Mac:** Docker Desktop disk limit set to 32 GB first; `build-images.sh` built all three images from the real ECR Public base, 176 MB each. Containers ran against real AWS with short-lived role credentials: worker checked all three sites, status page (served from the container) showed CloudForger and chess up, broken-test down.
- **Surprise (a nice proof).** The worker name in the logs became the container ID (`57d038f15bbc`) instead of `MacBookPro`, and the DNS error switched from macOS wording (`[Errno 8] nodename nor servname`) to Linux wording (`[Errno -2] Name or service not known`): visible evidence the code is now running inside a Linux container. In the cluster the same field will show the pod name.

**Phase 1 complete:** three services, 29 tests, three non-root images, verified locally and against real AWS.

---

## Phase 2: the temporary layer (network + EKS + nodes)

`infra/modules/vpc` (VPC module from an earlier project, extended), `infra/cluster` (VPC + EKS),
`infra/up.sh`, `infra/down.sh`.

**Network**
- Decision: reuse the VPC module from my earlier Terraform project (Aug 2026), copied in with an origin note. Rejected the community VPC module (equally valid; reuse shows continuity). Honest framing for the write-up: "a VPC module from an earlier project, extended for EKS".
- Change 1: optional `public_subnet_tags` / `private_subnet_tags` (default empty). EKS needs `kubernetes.io/role/elb` and `kubernetes.io/role/internal-elb` or the load balancer controller silently finds no subnets.
- Change 2: database route table only created when database subnets exist, plus a **`moved` block** (`aws_route_table.database` → `[0]`) so the original project's state keeps the table instead of replacing it.
- Change 3 (inputs only): private subnets `/20` (~4,000 addresses) instead of `/24`. Prefix delegation allocates contiguous `/28` blocks; small subnets fragment and fail with `InsufficientCidrBlocks` even with addresses free.
- All three changes are backward compatible; the diff against the original was reviewed line by line.
- Decision: one NAT gateway (`single_nat_gateway = true`). Trade-off: a NAT/zone outage cuts outbound traffic for both zones; acceptable for a lab, not for production.

**Cluster**
- Decision: community EKS module, pinned to **21.29.0** (exact version, not a range). Module source was cloned and every input checked against its `variables.tf`, including nested node group / add-on / access-entry fields. The checker caught the v20 name `cluster_version` in a deliberate test (v21 renamed it `kubernetes_version`).
- Decision: Kubernetes **1.36**, one behind the newest (1.37, released on EKS Oct 1 2026). Rejected 1.37: Helm charts and add-ons typically lag a new minor by weeks. Upgrading later is a planned exercise.
- Decision: `upgrade_policy = STANDARD`. AWS defaults clusters to EXTENDED support, which silently bills 6× the hourly rate after standard support ends.
- Decision: API endpoint public **only to my current IP** (`/32`, detected by `up.sh`) plus private access inside the VPC. The module's default is private-only (my Mac couldn't connect); the common tutorial value `0.0.0.0/0` is rejected by a validation rule.
- Decision: `authentication_mode = API` (access entries only) and an explicit admin entry for `OrganizationAccountAccessRole`, with `enable_cluster_creator_admin_permissions = false`, so a future CI apply can never make CI a cluster admin.
- Decision: `enable_irsa = false` (Pod Identity instead, so no per-cluster OIDC provider).
- Decision: no customer-managed KMS key. EKS envelope-encrypts all Kubernetes API data by default with an AWS-owned key (since March 2025). Rejected a CMK: $1/month plus a new key per rebuild, for no added value in a lab.
- Decision: control-plane logs `audit` + `authenticator` only, 7-day retention. `api` logs left off: very chatty and CloudWatch bills per GB.
- Decision: prefix delegation via the `vpc-cni` add-on with `before_compute = true`. Per AWS docs, managed node groups calculate max pods automatically once the CNI is configured, but the add-on must exist **before** nodes join or they keep the old limit (17 on t3.medium; 110 with prefixes).
- Decision: 2× t3.medium, on-demand, AL2023 x86. Rejected Spot for now (cheaper, but interruptions would muddy early debugging; a later cost-optimisation option).
- Kept module security defaults deliberately: IMDSv2 required, hop limit 1, so pods can't use the node's own AWS credentials.
- Decision (caught in review): account guard as a **postcondition on the account lookup**, not `depends_on` on the modules. `depends_on` on a module defers every data source inside it to apply time, producing noisy and sometimes phantom plan diffs.

**Scripts**
- `up.sh`: account check, detects public IP, `terraform apply`, writes kubeconfig (context `patrol`, profile baked in), waits for nodes Ready, prints elapsed time (the rebuild-time metric).
- `down.sh` order: delete Argo CD apps (Phase 3+), Ingresses and LoadBalancer Services through Kubernetes → wait until AWS confirms the controller-created load balancers are gone → `terraform destroy` → check for anything still tagged `Layer=cluster`. Load balancers made by Kubernetes are unknown to Terraform; destroying first orphans them (still billing, and they block VPC deletion). Same orphan pattern as the chess migration.
- **Bug (caught before delivery).** `[[ -n "$ns" ]] && kubectl …` inside a piped loop returns non-zero on an empty line; with `set -e` + `pipefail` that would have aborted `down.sh` mid-cleanup. Fixed with an explicit `if`.
- Verification: ShellCheck 0.11 clean on every script in the repo. `down.sh` run end to end against stand-in `aws`/`kubectl`/`terraform` commands: correct order, waited through a load balancer that took two polls to disappear; and with a load balancer that never disappears it exited 1 **without running `terraform destroy`**.
- First `up.sh` plan: **50 to add**, 0 to change, 0 to destroy; Kubernetes version 1.36 confirmed in the outputs.
- **First cluster build: 50 added in 14m 34s** (apply + kubeconfig + nodes Ready). Two nodes Ready on `v1.36.4-eks`, Amazon Linux 2023, containerd 2.3.5, private IPs `10.20.30.118` and `10.20.32.116`: one in each /20 private subnet, so the copies can spread across both zones.
- **EKS console:** Active, 1.36, "Standard support until August 1, 2027" (the STANDARD upgrade policy, no silent extended support), 2 nodes, 1 node group, 4 add-ons, 8 pods, 0 health / upgrade / node issues.
- **Surprise.** The console still shows an "OpenID Connect provider URL" despite `enable_irsa = false`. Every EKS cluster has an OIDC *issuer* (it signs service-account tokens); IRSA additionally needs an IAM *OIDC identity provider* registered in the account pointing at it. `enable_irsa = false` skips only that IAM registration. Worth knowing for interviews: "issuer exists, IAM trust doesn't".
- **Prefix delegation confirmed:** `pods: 110` (Capacity and Allocatable) on the node, instead of t3.medium's default 17. All 8 system pods `Running`, 0 restarts.
- **Mistake (mine, in the check command).** `kubectl describe nodes | grep … | head -4` cut off after the first node's two `pods:` lines (Capacity + Allocatable), so it only proved node 1. Better check: `kubectl get nodes -o custom-columns=NODE:.metadata.name,MAX_PODS:.status.allocatable.pods` (one line per node).
- **Both nodes verified:** `10.20.30.118` → 110 pods, us-east-1a (subnet 10.20.16.0/20); `10.20.32.116` → 110 pods, us-east-1b (subnet 10.20.32.0/20). Node IPs land in the subnet planned for each zone.
- **First teardown: 50 destroyed in 12m 52s.** Full first cycle (build, verify, destroy) about 27 minutes of cluster time.
- **Bug (false alarm).** Symptom: `down.sh` reported a NAT gateway and two subnets "still tagged Layer=cluster" after a clean destroy. Investigation (systematic, before changing anything): the subnet IDs were the *public* subnets; AWS can't delete a VPC that still has subnets, so they couldn't exist. Confirmed directly: NAT gateway state `deleted`, both subnets `InvalidSubnetID.NotFound`, no VPC, no Elastic IP, no cluster. Cause: the Resource Groups Tagging API index lags deletions by minutes, and deleted NAT gateways stay visible (state `deleted`) for about an hour. Fix: `down.sh` now asks each service directly (EKS, EC2 instances, NAT gateways excluding `deleted`, Elastic IPs, VPC, controller-made load balancers) and prints an ok/LEFTOVER checklist. Tested both outcomes with stand-in commands; ShellCheck clean.
- Lesson: an index or cache answering "what exists?" is not the source of truth. For anything that bills, ask the service.

**Phase 2 complete:** cluster built from Terraform in 14m 34s, verified (2 nodes across 2 zones, 110 pods each, 8 healthy system pods, STANDARD support), destroyed in 12m 52s with nothing left over.

---

## Phase 3: Patrol running in the cluster (plan approved)

- Decision: **public GitHub repo `patrol`**, matching the other portfolio projects; Argo CD reads it without stored credentials. Nothing secret is ever committed (account IDs and queue URLs aren't secrets; the Slack webhook lives in Secrets Manager; `.gitignore` blocks state, tfvars, `.terraform/`, `.venv/`). Rejected a private repo (Argo CD would need a stored deploy key, and the code is portfolio material anyway).
- Decision: **HTTP first, HTTPS as the final step of Phase 3**: delegate only `patrol.cloudforger.app` from IONOS to a Route 53 zone (~$0.50/month), free ACM certificate validated once by DNS (both permanent layer), and **ExternalDNS** to repoint the record at each rebuild's new load balancer. Rejected a hand-made DNS record (breaks on every rebuild, since the load balancer's address changes). Debugging one thing at a time: plain HTTP must work before certificates enter the picture.
- Decision: **Argo CD installed by `up.sh` with Helm** (pinned chart version), then one root Application ("app of apps") installs everything else. Rejected installing it with Terraform's Helm provider: it blurs "Terraform owns AWS, Argo CD owns the cluster".
- Decision: **first images pushed from the Mac** by a script, tagged with the Git commit; Phase 4 automates exactly this in GitHub Actions.
- Decision: **free VPC gateway endpoints for DynamoDB and S3**. Found while learning: with no endpoints, a NAT gateway failure would break checks, the status page's DynamoDB reads, and image pulls (ECR stores layers in S3). Endpoints remove that single point of failure for DynamoDB/S3 and cut NAT data charges.
- Decision: **IAM roles in the permanent layer, Pod Identity associations in the cluster layer**. Roles don't depend on the cluster (Pod Identity trust is cluster-agnostic), so they survive every teardown; only the associations are rebuilt.
- Design rule (from the identity lesson): the pod is the security boundary; anything needing different permissions gets its own pod and ServiceAccount.

- **Repo created (pre-commit check).** Safety check before the first commit flagged `infra/bootstrap/terraform.tfvars.example`; verified its staged content was only the placeholder (`you@example.com`) before pushing, rather than assuming either way. Staged files: 54, including the three `.terraform.lock.hcl` files (provider versions pinned in Git) and no real `terraform.tfvars`, state, `.terraform/` or `.venv/`.
- **Pushed:** first commit `a8577cf` to `github.com/stevenwaldron/patrol` (public), 66 objects, 52.5 KiB.

### Phase 3, step 2: Pod Identity roles, associations, gateway endpoints

- **Permanent layer:** 4 IAM roles + 4 managed policies + 4 attachments (scheduler, worker, status-page, aws-load-balancer-controller).
- Decision: trust policy per AWS docs (`pods.eks.amazonaws.com`, `sts:AssumeRole` + `sts:TagSession`) **plus conditions pinning `aws:RequestTag/kubernetes-namespace` and `aws:RequestTag/kubernetes-service-account`**. A role can only be used by its one intended ServiceAccount, even if a wrong association were created. Rejected the bare trust policy (any association could use any role).
- Decision: least privilege from the code's actual calls: scheduler `sqs:SendMessage` (covers SendMessageBatch); worker `sqs:ReceiveMessage` + `sqs:DeleteMessage` + `dynamodb:PutItem`; status page `dynamodb:Query`. Each scoped to the one queue or table ARN.
- Decision: load balancer controller pinned to **v2.17.1** (latest release tag, checked with `git ls-remote`), using the **official policy file from that exact tag**, stored as `policies/aws-load-balancer-controller-v2.17.1.json`. Upgrading the controller means replacing the file too. Size checked: 5,196 chars against the 6,144 managed-policy limit.
- Decision: managed policies (`aws_iam_policy`) rather than inline: the controller's policy is large, and managed policies are visible and reusable in the console.
- **Cluster layer:** `aws_eks_pod_identity_association` for each role, read from the permanent layer's state (`terraform_remote_state`). Decision: one source of truth for the namespace/ServiceAccount pairing. Rejected duplicating the list in both layers (they could silently drift).
- **Cluster layer:** free gateway endpoints for DynamoDB and S3 on the private route tables (new `private_route_table_ids` output on the VPC module, backward compatible). Endpoint policy left at default: permissions are already decided by each pod's role, and a restrictive S3 endpoint policy would also need ECR's own layer buckets.
- Ordering dependency: the permanent layer must be applied before the cluster layer, which reads its outputs.
- **Applied:** plan exactly 12 to add, 0 to change, 0 to destroy (no drift on the existing 12 permanent resources); apply 12 added. `pod_identity_roles` output shows each role locked to its namespace/ServiceAccount (load balancer controller in `kube-system`, the three Patrol services in `patrol`).
- **Verified in the IAM console:** `patrol-worker` trust policy trusts only `pods.eks.amazonaws.com`, with `StringEquals` on `kubernetes-service-account = worker` and `kubernetes-namespace = patrol`. Screenshots 27–29.

### Phase 3, step 3: image push script + Patrol Helm chart

**`app/push-images.sh`**
- Refuses to run with uncommitted changes, so tag = commit always means "exactly this code". Tag length pinned with `git rev-parse --short=7` (also in `build-images.sh`), so both scripts always agree.
- Account guard, ECR login via short-lived token piped to `docker login --password-stdin` (never on screen or in history).
- Skips a tag already in ECR. Decision: skip rather than fail, because tags are immutable and re-running the script should be harmless.
- Builds any image not built locally, then prints ECR's scan-on-push severity counts and the exact `tag:` line to put in `values.yaml`.
- Verified with stand-in `aws`/`docker`/`git`: pushed the missing images, skipped the one already in ECR, built the one not built locally, exited 1 on an uncommitted change. ShellCheck clean.

**Chart `deploy/charts/patrol` (12 objects):** 3 ServiceAccounts, sites ConfigMap, scheduler CronJob, worker + status-page Deployments, 2 Services, 2 PodDisruptionBudgets, Ingress.
- Decision: one chart for the whole app, with shared snippets in `_helpers.tpl` (labels, security settings, AWS env, zone spread). Rejected copy-pasted manifests (the image tag alone would sit in 3 places).
- Decision: `values.schema.json` validates values before rendering: image tag must be 7–40 hex characters (so `latest` is rejected), site IDs must match the app's own rule, queue URL and registry formats checked.
- Decision: **render guards.** The chart refuses to render outside namespace `patrol` (the IAM roles only trust it) or without an image tag, each with a plain-English error. Both tested.
- Decision: `automountServiceAccountToken: false` on all ServiceAccounts and pods. No Patrol pod calls the Kubernetes API. Verified first in the EKS docs that Pod Identity delivers its own projected token (`eks-pod-identity-token`, audience `pods.eks.amazonaws.com`), so AWS access is unaffected.
- Decision: security on every pod: non-root uid 10001, `seccompProfile: RuntimeDefault`, no privilege escalation, all capabilities dropped, **read-only root filesystem** (small capped `emptyDir` at `/tmp` in case a library needs scratch space). These are exactly the rules Kyverno will enforce in Phase 6.
- Decision: CPU and memory requests on everything, memory limits, **no CPU limits**. A CPU limit throttles a container even when the node has idle CPU; memory limits stop a leak from taking the node down. Trade-off: one busy pod can use spare CPU (acceptable; requests still guarantee everyone their share).
- Decision: zone spread with `ScheduleAnyway` (prefer, don't insist). Rejected `DoNotSchedule`: if a zone's node is unhealthy but still registered, a replacement pod could stay Pending. Availability first; Phase 8 drills will test it.
- Decision: PodDisruptionBudgets use `maxUnavailable: 1`, not `minAvailable: 1`. At 1 replica (possible once KEDA scales the worker), `minAvailable: 1` would block every node drain.
- Decision: rolling updates `maxSurge: 1, maxUnavailable: 0`: a new copy must be Ready before an old one leaves.
- Scheduler CronJob: `concurrencyPolicy: Forbid`, `startingDeadlineSeconds: 30`, `backoffLimit: 0` (a failed run is replaced by the next minute's run; retries would issue duplicate orders), 50 s deadline, UTC time zone, finished jobs cleaned up after 10 min.
- Worker: `terminationGracePeriodSeconds: 60` (20 s long poll + slowest check) so the SIGTERM handler can finish its batch; probes on `/health` (503 if the poll loop stalls for 80 s).
- Status page: readiness `/ready`, liveness `/health`, and a **preStop sleep of 10 s** (native Kubernetes sleep action, no shell needed) so the ALB stops sending traffic before the pod exits. ALB deregistration delay cut from 300 s to 30 s.
- Decision: Ingress in **IP target mode**, internet-facing, HTTP only for now. **`/metrics` blocked at the ALB** with a fixed 404 response, so internal metrics are never public. Load balancer named `patrol`, tagged `Project=patrol,Layer=cluster`.
- Decision: site list lives in two places (Helm can only read files inside the chart; local runs use `config/sites.yaml`). New test `test_chart_sites.py` fails if they drift. Mutation-checked: changing one timeout in the chart made it fail.
- Worker gets a Service too (port 9100, `metrics`), only so Prometheus can find it in Phase 5.
- **Verification (workspace):** Helm 4.3.1 + kubeconform 0.8.0 (checksums verified; `get.helm.sh` is blocked there, so Helm came from the conda-forge build). `helm lint` clean; `helm template` renders 12 objects; **kubeconform strict against Kubernetes 1.36: 12 valid, 0 invalid**. A cross-check script confirmed every ServiceAccount matches a Terraform Pod Identity role in namespace `patrol`, every image matches an ECR repo, every `PATROL_*` variable exists in `config.py`, and ports match the Dockerfile. Tests: 30 passed.
- **Surprise.** Helm 4 is now the current major version (4.3.1); most tutorials still show Helm 3. Charts use the same `apiVersion: v2` format.
- **On the Mac:** 30 passed in 3.34 s, including the new chart-sites test (screenshot 30).
- **First real push:** all three images pushed as `f3e30bf` (screenshots 31–32). ECR scan on push, identical for all three images: **3 HIGH, 9 MEDIUM, 3 LOW**. Identical counts across three different services point at the shared base image (Debian packages in `python:3.13-slim`), not at Patrol's code; triage next.
- **Surprise.** The worker image is 72.5 MB in ECR versus 176 MB locally: ECR reports the compressed layer size, Docker Desktop the unpacked size.
- **Vulnerability triage (HIGH findings, screenshot 33).** Hypothesis confirmed: all three are in Debian base packages, not Patrol code.
  - `CVE-2026-19499`, glibc 2.41-12+deb13u4: `strfmon`/`strfmon_l` (money formatting) can overflow the caller's buffer. Debian: trixie **vulnerable, no fix**, marked `no-dsa (Minor issue)`; fix only in forky/sid. Patrol never calls `strfmon` (Python's `locale.currency` is implemented in Python). **Not reachable.**
  - `CVE-2026-85091`, zlib 1.3.1: heap overflow in `gz_vacate()` during non-blocking `gzwrite()`/`gzprintf()`. Debian: trixie listed **vulnerable, no fix** (fixed only in unstable). Python's `gzip` module uses zlib's raw deflate/inflate, not the `gz*` file functions, and Patrol doesn't compress anything. **Not reachable.**
  - `CVE-2026-5435`, glibc: resolver's `ns_sprintrrf` (formats DNS records as text, used by DNS debugging tools). NVD rates it 7.3; SUSE rates it 5.3. Python resolves names with `getaddrinfo`, which doesn't use it. **Not reachable.** Debian status not confirmed (tracker page not reachable from the build workspace).
  - Lesson: a scanner's severity is about the package, not about your use of it. Triage asks three questions: is it in my code or the base? Is a fix available? Is the vulnerable function actually reachable?
- **Surprise (proof of the drift problem).** `docker pull python:3.13-slim` reported "Downloaded newer image": the name now points to a different image (`sha256:d98cc29d…0962`) than the one `f3e30bf` was built from, only days later. Same glibc (`2.41-12+deb13u4`, so no fix yet), but zlib went from `1.3.1-1` to `1.3.1-1+b1` (a Debian rebuild). So the same commit would already build a different image, and the push script would skip it because the tag exists. Screenshot 34.
- Decision: **base image pinned by digest** (`python:3.13-slim@sha256:d98cc29d…0962`). Rejected pinning by name only: the name moves (proven above), which breaks "one tag = one exact build" and makes the push script silently skip real updates. Base updates are now reviewed one-line commits (Dependabot/Renovate can open them later). Trade-off: security fixes in the base no longer arrive automatically; someone (or a bot) has to bump the digest.
- Decision: `f3e30bf` is not deployed (built from the unpinned base). First deployed tag will be the digest-pin commit.
- Decision (for Phase 4): Trivy fails the build on **HIGH/CRITICAL findings with a fix available** (`--ignore-unfixed`); unfixed findings are recorded with a triage note, like the three above. Rejected failing on every HIGH: it would block every build for issues nobody can fix yet.
- **Mistake (process).** The deploy-tag commit (`4720f42`, `tag: "f3e30bf"`) went in before the digest-pin zip had been synced, so it deployed the unpinned build. Caught by checking `git log` and `grep -c` on the Dockerfile (0 = pin missing). Harmless (no cluster yet); fixed by the next deploy commit, which supersedes it. Lesson: verify each step's output before running the next; a check that prints a number is only useful if someone reads the number.
- **Pinned build deployed in Git:** pin commit `1ec1100` → images `1ec1100` built from the pinned base and pushed (same 3 HIGH: no Debian fix yet, as triaged). Deploy commit `cbd5865` changes one line, `tag: "f3e30bf"` → `"1ec1100"` (screenshots 35–36). First "upgrade = one reviewed line" in the project.

### Phase 3, step 4 prep: version correction (load balancer controller)

- **Mistake (mine).** I recorded v2.17.1 as the controller's "latest release". The real latest is **v3.6.2**. My `git ls-remote` tag filter was wrong, and I didn't cross-check it against the official Helm chart (eks-charts `stable/aws-load-balancer-controller`: chart 3.6.2, appVersion v3.6.2). Caught while preparing the Argo CD install, before anything used it.
- Impact check: the v3.6.2 `docs/install/iam_policy.json` is **byte-identical** to the v2.17.1 file (16 statements, 5,196 chars), so the role's permissions don't change. v3.0.0 mainly aligned the Helm chart's version with the controller's (v2.17 was chart 1.17), made Gateway API GA, and removed the chart's KeepTLS option; nothing affecting Ingress use. Requires Kubernetes 1.22+.
- Fix: policy file renamed to `aws-load-balancer-controller-v3.6.2.json`; version removed from the IAM descriptions so future upgrades don't churn IAM.
- Lesson: check a "latest version" against two independent sources (release tags **and** the official chart/registry).

### Phase 3, step 4: Argo CD + load balancer controller (GitOps takes over)

- Decision: **`up.sh` installs Argo CD with Helm** (chart argo-cd 10.11.6 = Argo CD v3.5.6, settings in `deploy/argocd/values.yaml`), then applies one file, the **root app**. Everything else in the cluster comes from Git. Argo CD itself is the one hand-installed piece (bootstrapping: something has to install the installer). Rejected Argo CD managing its own install for now (adds a self-upgrade loop to debug; possible later).
- Decision: dashboard **private**: Service stays ClusterIP, reached only with `kubectl port-forward` from my Mac. Rejected exposing it through a load balancer (an admin UI on the internet). Dex (single sign-on) and notifications disabled; requests/limits set on every Argo CD component.
- Decision: **app of apps.** `root-app.yaml` watches `deploy/argocd/apps/`, which holds two Applications: the AWS Load Balancer Controller (AWS's official chart 3.6.2, pinned) and Patrol (this repo's chart). Adding a cluster component = adding a file. Auto-sync with **prune** and **self-heal** on all of them.
- Decision: **sync waves** (controller -1, Patrol 0) **plus an Application health check** in Argo CD's config. Surprise found while designing: since Argo CD 1.8, a parent app doesn't wait for a child Application to be *healthy*, only created, so waves alone wouldn't guarantee the controller is running before Patrol's pods start. Added the documented Lua health customization so waves really wait.
- Decision: Patrol's namespace gets the label `elbv2.k8s.aws/pod-readiness-gate-inject: enabled` (through Argo CD's `managedNamespaceMetadata`). The controller's webhook adds a readiness gate to each new pod, so a rolling update waits until the **ALB itself** reports the new pod healthy. That's why the controller must be up first.
- Decision: the controller finds the VPC **by its Name tag** (`vpcTags: {Name: patrol-lab-vpc}` → `--aws-vpc-tags=Name=patrol-lab-vpc`). Pods can't ask the node's metadata service (IMDSv2 hop limit 1, on purpose), and the VPC ID changes on every rebuild so it can't live in Git; the tag never changes. Two earlier decisions meeting.
- Decision: controller with 2 replicas (its webhook must answer whenever Patrol pods are created), `enableServiceMutatorWebhook: false` (Patrol uses an Ingress; don't take over every LoadBalancer Service), ServiceAccount name pinned to the one the Pod Identity role trusts.
- **`up.sh`** now waits for each app to be Synced + Healthy (printing each change of state), then for the status page to answer through the ALB, and reports two times: cluster build, and Argo CD + apps after the nodes were Ready. It prints the dashboard steps but **never prints the admin password**.
- **`down.sh` teardown order (design flaw caught before first use).** The old version deleted all Argo CD apps at once: the controller could be removed before it deleted the ALB, leaving an orphaned, billing ALB that blocks VPC deletion. New order: (1) detach the root app without cascading (remove its finalizer, delete it) so it can't recreate children; (2) delete the Patrol app (its finalizer makes Argo CD delete the Ingress, and the still-running controller deletes the ALB); (3) safety net: delete any other Ingress/LoadBalancer Service; (4) wait until AWS confirms no load balancers remain; (5) only then delete the remaining apps; then `terraform destroy`.
- **Verification (workspace).** Chart repos (`*.github.io`) are blocked there, so both charts were rendered from their source at the pinned versions (argo-helm tag `argo-cd-10.11.6`; eks-charts commit publishing 3.6.2). Argo CD: 44 objects, image `argocd:v3.5.6`, all Services ClusterIP, notifications absent, health customization present. Controller: 11 objects, image `public.ecr.aws/eks/aws-load-balancer-controller:v3.6.2`, args `--cluster-name=patrol --aws-region=us-east-1 --aws-vpc-tags=Name=patrol-lab-vpc`, ServiceAccount `aws-load-balancer-controller`. kubeconform strict, Kubernetes 1.36: 52 valid, 0 invalid (4 CRDs skipped). The three Application files validated **strictly against Argo CD's own Application schema** (unknown fields rejected); a deliberate typo (`selfHeel`) was caught. `down.sh` run against stand-in commands: correct order end to end; with an ALB that never disappears it exits 1 **before** deleting the controller (so a rerun can retry) and before `terraform destroy`. ShellCheck clean.
- Permanent plan for the v3.6.2 rename: exactly as predicted, **2 to add, 1 to change, 2 to destroy** (controller policy + attachment replaced because an IAM policy's description can't be edited in place; role description updated). Screenshot 37.
- Helm 4.3.1 installed on the Mac from the official binary, checksum verified (`OK`).
- **First full GitOps bring-up: everything up in 17m 56s.** Argo CD + apps took **4m 24s** after the nodes were Ready. Sync waves worked as designed: `aws-load-balancer-controller` went OutOfSync/Progressing → Synced/Progressing → **Synced/Healthy before Patrol started**, then `patrol` → Synced/Healthy. Status page answered through the ALB at `patrol-…us-east-1.elb.amazonaws.com` on the first check. Screenshot 38.
- That one line proves the whole chain: Pod Identity (controller created an ALB; Patrol pods became healthy), VPC found by tag, readiness gates, the chart's probes, and the pinned `1ec1100` images.
- **Surprise.** `kubectl apply` of the root app printed a warning: Kubernetes 1.36 prefers finalizer names that contain a path (`/`). Argo CD supports `resources-finalizer.argocd.argoproj.io/foreground` (same behaviour as the bare name: delete resources first, then the app). Harmless; to switch over in the next change.
- **Live in EKS (screenshots 39–42).** Status page through the ALB: CloudForger and chess Operational, broken-test Down (`[Errno -2]`, Linux wording). Worker and status-page copies each spread across both nodes. Argo CD dashboard: root, aws-load-balancer-controller (eks-charts 3.6.2) and patrol (this repo, `deploy/charts/patrol`) all Healthy + Synced; patrol tree shows all 12 chart objects and exactly 3 finished scheduler Jobs (the history limit).
- **Surprise.** Check latency fell from ~300 ms (Mac, across the internet) to 30–82 ms (pods in us-east-1, close to CloudFront).
- **Investigation: `READINESS GATES <none>` on the first pods.** Hypothesis: the controller's webhook only adds the gate when a pod is created, and only if a TargetGroupBinding for its Service already exists; on first bring-up the pods and the Ingress were created together, before the controller had built the ALB's target group. Test: confirmed the namespace label (`elbv2.k8s.aws/pod-readiness-gate-inject=enabled`) and the TargetGroupBinding (`k8s-patrol-statuspa-…`, target type ip), then `rollout restart`. **New pods show `1/1` readiness gates; confirmed** (screenshot 43). Every rollout from now on waits for the ALB's own health check. Only the very first pods after a rebuild lack the gate.
- **Finding (from the same screenshot): both new status-page pods landed on the same node** (`ip-10-20-33-141`). Cause: the zone/node spread rule counts *all* status-page pods, including the old ReplicaSet's pods still terminating, so mid-rollout the scheduler sees the zones as already balanced. And `ScheduleAnyway` is only a preference. Fix (next change): `matchLabelKeys: [pod-template-hash]` so the spread rule only counts pods of the same rollout. A real failure-drill-style finding: after any redeploy, one zone outage could have taken the status page down.
- **First real run of the new teardown order: 56 destroyed, cluster down in 12m 50s, every leftover check ok** (screenshots 44a, 44). No orphaned ALB.
- **Fix-up (chart 0.1.1).** Topology spread now uses `matchLabelKeys: ["pod-template-hash"]` on both rules, so spreading only counts pods from the same rollout (the old ReplicaSet's terminating pods no longer skew placement). kubeconform strict 1.36: 12/12 valid. To verify live: after a rollout, the two status-page copies should sit on different nodes.
- **Fix-up.** Argo CD finalizers renamed to `resources-finalizer.argocd.argoproj.io/foreground` (silences the Kubernetes 1.36 finalizer-name warning). Verified in the Argo CD v3.5.6 source (`application_defaults.go`, `isPropagationPolicyFinalizer`) that it's a recognised cascade-delete finalizer with the same foreground behaviour, so `down.sh` is unaffected.
- Decision: **HTTPS with full automation on a NEW domain** (not cloudforger.app: Patrol gets its own identity), **bought at IONOS** and delegated to Route 53. Rejected registering through Route 53 (the yearly fee lands in one month and would trip the $30 budget's alerts, making them meaningless that month). Rejected skipping HTTPS (an unstable link that changes every rebuild and shows "Not secure").

### Phase 3, step 5: HTTPS (mypatrolapp.cloud)

- Domain **`mypatrolapp.cloud`** registered at IONOS ("domain only": ACM provides the certificate for free, Route 53 runs DNS). `.cloud` is not on the HSTS preload list, so plain HTTP keeps working during the build. Renewal is billed by IONOS, outside the AWS budget.
- **Permanent layer (`dns.tf`):** Route 53 public hosted zone (**`prevent_destroy`**: recreating it would change its name servers and silently break the IONOS delegation), ACM certificate for `mypatrolapp.cloud` + `*.mypatrolapp.cloud` validated by DNS (validation records in the zone, so ACM renews it by itself).
- Decision: **no `aws_acm_certificate_validation` resource.** It would make the apply wait for the IONOS delegation, which is a manual step that can only happen *after* the apply (it needs the zone's name servers). ACM issues the certificate on its own once the delegation spreads; `up.sh` refuses to start a cluster until it's ISSUED.
- Decision: **ExternalDNS** (chart 1.23.0 = v0.23.0; v0.24.0 exists but has no chart yet, so the chart's own app version was kept rather than mixing versions). Checked "latest" two ways (git tags + chart's `Chart.yaml`), per the earlier lesson. Settings: source = Ingress only; `domainFilters: [mypatrolapp.cloud]`; public zones only; **`policy: sync`** (also deletes records whose Ingress is gone, so teardown doesn't leave the domain pointing at a dead ALB) made safe by the **TXT ownership registry** (`txtOwnerId: patrol`): it only ever changes records it created.
- Decision: **ExternalDNS IAM narrowed** from the official guide: record changes, reads and tag lookups only on **Patrol's zone ARN** (the guide allows every hosted zone in the account); `ListHostedZones` stays `*` (it can't be scoped). Fifth Pod Identity role (`external-dns/external-dns`); the cluster layer's association is generated automatically from the permanent layer's role list.
- Decision: **certificate found by hostname**, not by ARN in Git. The controller discovers the ISSUED ACM certificate matching the Ingress host (its official policy includes `acm:ListCertificates` + `acm:DescribeCertificate`, checked). Keeps account-specific ARNs out of the chart.
- **Chart 0.2.0:** `ingress.host` (validated by the schema; empty = previous HTTP-only behaviour, both modes render and pass kubeconform strict 1.36). With a host: listeners 80 + 443, **HTTP→HTTPS redirect** (`ssl-redirect: 443`), TLS policy `ELBSecurityPolicy-TLS13-1-2-2021-06` (TLS 1.3/1.2 only). `/metrics` stays blocked at the ALB.
- **`up.sh`:** (1) before any Terraform, refuses to build unless the domain's certificate is ISSUED (no paying for a cluster that can't serve HTTPS); (2) at the end, waits for ExternalDNS's A record to appear **in Route 53** before ever looking the name up, avoiding negative DNS caching (a lookup that finds nothing can be cached as "doesn't exist" for up to 15 minutes), then checks `https://mypatrolapp.cloud/ready`.
- **Mistake (mine, caught).** My check for ExternalDNS's `--aws-zone-type=public` argument found nothing; the chart quotes list items (`- "--aws-zone-type=public"`), and my grep pattern didn't allow for quotes. The argument was there. Lesson: when a check fails, question the check before the thing being checked.
- **Verification (workspace).** ExternalDNS rendered from its source at the pinned tag: image `registry.k8s.io/external-dns/external-dns:v0.23.0`, ServiceAccount `external-dns`, args `--source=ingress --policy=sync --registry=txt --txt-owner-id=patrol --domain-filter=mypatrolapp.cloud --provider=aws --aws-zone-type=public`, non-root, read-only root filesystem; kubeconform strict 5/5. Terraform reference checks: permanent 14 resource blocks, cluster unchanged, nothing unresolved. ShellCheck clean.
- Permanent plan for HTTPS: **9 to add, 1 to change, 2 to destroy** = the 7 new resources (zone, certificate, 2 validation records, ExternalDNS role/policy/attachment) **plus** the still-unapplied v3.6.2 policy swap from step 4 (2 add, 1 change, 2 destroy). Explained exactly by the two pending changes before applying (screenshot 45). Lesson: a saved plan that's never applied quietly carries over into the next one; reading every line of the summary caught it.
- Applied: Route 53 zone with 4 name servers spread across `.org`, `.co.uk`, `.com`, `.net` (screenshot 46). IONOS name servers switched to them, "can take up to 48 hours", IONOS's own DNS zone deactivated (screenshot 47). Certificate stays `PENDING_VALIDATION` until the delegation spreads.
- **Surprise.** The delegation was live about **1 minute** after saving at IONOS (despite "up to 48 hours"): `dig NS` already returned all 4 awsdns name servers. A brand-new domain has no old answers cached anywhere, so there's nothing to wait out. Certificate: `PENDING_VALIDATION` (ACM re-checks the validation record on its own schedule). Screenshot 48a.
- **Certificate ISSUED** (both validations SUCCESS) about 40 minutes after the delegation went live (screenshot 48). Checked directly with `aws acm describe-certificate` rather than through Terraform state.
- **🎉 HTTPS live: https://mypatrolapp.cloud** (screenshots 49–51). `up.sh`: certificate check passed; ExternalDNS created `mypatrolapp.cloud -> patrol-1836063828.us-east-1.elb.amazonaws.com.` by itself; status page answered over HTTPS. Browser: "Connection is secure", "Certificate is valid". `curl`: `http://` → **301 Moved Permanently** (Server: awselb/2.0); `/metrics` → **404** (blocked at the ALB); **TLSv1.3**, subject `CN=mypatrolapp.cloud`, issuer `Amazon RSA 2048 M01`. Latency 21–61 ms.
- **Open question (to investigate before teardown): "Argo CD + apps took 41m 34s after the nodes were Ready"** (previous run: 4m 24s). The app states printed look normal, so the time went somewhere the script doesn't report. Gathering evidence (app sync timestamps, ExternalDNS pod restarts/logs, root app operation history) while the cluster is still up, rather than guessing.
- Screenshot note: the browser screenshots show my profile photo in Chrome's toolbar; redact before the write-up.
- **Investigation: where did 41 minutes go? (evidence, screenshot 49b)** Timeline from the cluster itself (UTC):
  - 05:25:28 nodes Ready
  - 05:26:51 all Argo CD pods started (0 restarts) → Argo CD itself was up ~1.5 min after the nodes
  - **05:59:15 root app created**: `up.sh` only applies it after `helm upgrade --install --wait` returns, so **~32 minutes were spent inside the Helm install step**, with Argo CD already running
  - 05:59:17–05:59:27 root synced; 05:59:22–25 external-dns synced; 05:59:36–37 controller synced; 06:01:09–12 patrol synced → **all four apps in ~2 minutes** (GitOps itself is fast)
  - ~06:01 → ~06:07: ALB + ExternalDNS record + HTTPS answer (~6 min, expected)
  - ExternalDNS healthy: 0 restarts, "All records are already up to date" every minute.
  - Conclusion so far: **not** ExternalDNS, not the sync waves. The delay is between Argo CD's pods running and Helm's `--wait` returning. Leading hypothesis: the Mac went to sleep (run started around 01:11 local, unattended), pausing the `helm` process; Go timers on macOS don't advance during sleep, so the 10-minute timeout wouldn't fire either. Alternative: Helm 4's new default wait strategy (`watcher`) waiting on some resource. To be confirmed.
  - **Confirmed:** I closed the MacBook lid during the run, so the Mac slept and paused `helm --wait` (and `up.sh` with it). The cluster kept running and billing in AWS the whole time; only the script on my laptop stopped. Root cause: a long-running local script driving cloud infrastructure, with nothing keeping the machine awake. Lesson: the automation is only as reliable as the machine running it (one reason Phase 4 moves deploys to CI runners).
- **Spread fix verified live (screenshot 53).** After `rollout restart`, the two new status-page pods landed on **different nodes** (`ip-10-20-43-64`, `ip-10-20-21-38`), both with readiness gates **1/1**, while the old pod was still terminating: the exact situation that put both copies on one node before `matchLabelKeys`.
- **Unexpected (screenshot 52):** Route 53 shows the ExternalDNS **A alias record** (`mypatrolapp.cloud. → patrol-1836063828…elb.amazonaws.com.`) but **no TXT ownership records** in the A/TXT query. Expected TXT records containing `owner=patrol`. Matters because `policy: sync` only deletes records it can prove it owns. Investigating before teardown (full record list + ExternalDNS's own logs of what it created).
- **Bug: ExternalDNS never created its TXT ownership records** (screenshot 52b). Evidence: zone holds only A, AAAA (IPv6 alias, a bonus), NS, SOA and the ACM CNAME; ExternalDNS logged `CREATE mypatrolapp.cloud A`, `CREATE mypatrolapp.cloud AAAA`, "2 record(s) were successfully updated", with config `Registry:txt TXTOwnerID:patrol`. Traced through the v0.23.0 source rather than guessing:
  - `registry/txt`: on create, the registry *does* append a TXT record per record (skipped only if one already exists; none did).
  - `provider/aws`: every change sent to Route 53 is logged at info level, and only A/AAAA appear, so the TXT records were dropped in between: `changesByZone` skips any record whose name matches no managed zone, **at debug level**.
  - `registry/mapper.ToTXTName`: with no prefix, the TXT name is `"<type>-" + first label + rest`, so for the **apex** `mypatrolapp.cloud` it becomes **`a-mypatrolapp.cloud`**: a *different domain* under `.cloud`, outside the zone. Silently skipped.
  - Known upstream: kubernetes-sigs/external-dns **#4234** ("New format txt registry records fail for Apex record"), still open; reproduced in v0.14–v0.21 and now v0.23.0.
- Impact (why it matters): with no ownership record, ExternalDNS treats the A/AAAA records as **not its own**, so `policy: sync` won't delete them at teardown, and **the next rebuild's ExternalDNS won't repoint them to the new ALB** (the domain would stay on a deleted load balancer).
- Decision (fix): `txtPrefix: "%{record_type}."` → ownership records `a.mypatrolapp.cloud` / `aaaa.mypatrolapp.cloud`, inside the zone (traced through `ToTXTName` and the reverse `dropAffixExtractType`; the workaround confirmed by two users on #4234). Rejected: moving Patrol to a subdomain (avoids the bug but changes the public URL); the DynamoDB registry (unverified in the thread, one more component); downgrading to v0.13.5 (old, unpatched). One-time cleanup needed: delete the current unowned A/AAAA records by hand after teardown.
- Lesson: "no errors in the log" isn't proof of success. The skip only logs at debug level; the check that caught it was comparing what *should* exist (A + TXT) with what *does*.
- **Prediction confirmed.** Teardown: 57 destroyed, cluster down in 18m 57s, every leftover check ok (screenshot 54). Afterwards the zone **still held the apex A and AAAA records** (screenshot 55), exactly as predicted: with no ownership records, ExternalDNS's `sync` policy wouldn't delete records it couldn't prove were its own. One-time cleanup: deleted A + AAAA in the Route 53 console, kept NS, SOA and the ACM validation CNAME (needed for renewals) (screenshot 56).
- **Fix applied:** `txtPrefix: "%{record_type}."` in the ExternalDNS app. Rendered from the v0.23.0 chart source: argument `--txt-prefix=%{record_type}.` present. To verify next session: TXT records `a.mypatrolapp.cloud` / `aaaa.mypatrolapp.cloud` with `owner=patrol` appear, and the A/AAAA records are gone after `down.sh` with no manual cleanup.
- Decision (mine): **no `caffeinate` or stage timestamps in the scripts.** I'll keep the MacBook open and awake during `up.sh`/`down.sh` myself. Trade-off accepted: a closed lid pauses the script (the cluster keeps billing until it resumes); Phase 4 moves deploys to CI runners anyway.
- **Ownership fix verified live (screenshot 57):** the zone now holds TXT records **`a.mypatrolapp.cloud`** and **`aaaa.mypatrolapp.cloud`**, inside the zone, next to the A/AAAA records ExternalDNS created.
- **"Status page not answering" at the end of `up.sh`: layer-by-layer check ~15 min later.** Local DNS and the AWS nameserver both returned the ALB's IPs (32.200.130.49/.73); ALB `active`; HTTPS via normal DNS **200**; HTTPS straight to the ALB with DNS bypassed (`curl --resolve`) **200**. Everything worked, so the script's 10-minute check failed on something transient on the Mac's side, most likely a cached "no such name" answer (a browser tab still open on the status page from the last session would keep looking the name up while it didn't exist). Not provable after the fact.
- **The 12 minutes explained by the timeline:** nodes Ready 15:09:03 → Argo CD pods 15:10:32 → root app 15:10:53 → patrol synced 15:11:45. **All apps done ~2m 40s after the nodes.** The rest of the 12m 08s was `up.sh` waiting for the record (up to 5 min) and then the HTTPS check timing out (10 min). So the slow number measured the failed check, not Argo CD.
- **New question from the same timeline:** the controller app's *last* sync finished at 15:15:01, **after** Patrol's (15:11:45), although the controller installs first (wave -1). Hypothesis: a later automatic re-sync. The controller's chart generates its webhook certificate during templating, so every render differs, and Argo CD may see endless drift and keep re-syncing (a known pattern with charts that generate certificates). Checking its sync history before deciding anything.
  - **Hypothesis rejected (screenshot 57b):** the controller app has exactly **one** deploy in its history (15:11:03), is `Synced`, and has **no OutOfSync resources**. No re-sync loop, so no `ignoreDifferences` needed. The later "finished" time (15:15:01) is when that one sync *operation* completed, not a second sync. Kept the evidence; changed nothing.
- **Teardown with the ownership fix: 57 destroyed, 16m 08s, all ok (screenshot 58), but A, AAAA and both TXT records were still in the zone afterwards (screenshot 59).** So ownership now exists, yet ExternalDNS still didn't clean up.
  - Cause (from reading `down.sh` against ExternalDNS's settings): a **race**. ExternalDNS checks every **1 minute** (`interval: 1m`, not event-driven). `down.sh` deletes the Patrol app (Ingress gone) → waits only for the **load balancer** to disappear (which can take seconds) → then deletes the remaining apps, **including ExternalDNS**. ExternalDNS can be gone before its next 1-minute pass ever notices the Ingress is gone. Nothing in the script waited for DNS.
  - Difference from last time: then, ExternalDNS *couldn't* delete (no ownership); now it *could*, but wasn't given the time.
  - Not harmful this time: the leftover records carry `owner=patrol`, so the next rebuild's ExternalDNS will recognise them as its own and repoint them to the new ALB (a free extra test of the ownership fix). No manual cleanup needed.
- **Fix for the teardown race (both parts):**
  1. ExternalDNS `triggerLoopOnEvent: true` → renders as `--events` (checked in the v0.23.0 chart): it reacts to Ingress changes immediately instead of only on its 1-minute loop.
  2. `down.sh` now **waits for DNS** the way it already waits for the ALB: after the Patrol app is deleted and the load balancers are gone, it polls Route 53 (exact-name zone match) until `mypatrolapp.cloud` has no A/AAAA records, for up to 3 minutes, and only then deletes ExternalDNS and the controller. On timeout it **warns and continues** (records are owned, so the next `up.sh` repoints them; billing matters more than tidy DNS).
  - Decision: warn rather than fail. Rejected failing the teardown on leftover DNS (a cluster left running costs money; owned leftover records cost nothing and self-heal on the next rebuild).
  - Verified with stand-in commands: (1) records gone after 2 checks → "removed", then remaining apps deleted, then destroy; (2) records never gone → 18 checks (3 min), warning, teardown continues to `terraform destroy`; (3) no records → 1 check, continues. ShellCheck clean; ExternalDNS render kubeconform-valid.
- **Repoint verified (screenshot 60a).** The leftover owned records from the previous run pointed at the old ALB (`patrol-1586009495…`). On this rebuild ExternalDNS recognised them as its own (`owner=patrol`) and updated them to the new ALB: `mypatrolapp.cloud -> patrol-1834415071.us-east-1.elb.amazonaws.com.` Proves the ownership fix end to end: before it, ExternalDNS would have refused to touch those records.
- **"Status page not answering" a second time (screenshot 60)**, same as the previous run, which later answered fine at every layer. Two occurrences = a pattern, not a fluke. New hypothesis, **negative DNS caching from querying too early**: ExternalDNS creates the alias record as soon as the Ingress gets an ALB hostname, while the ALB is still provisioning; `up.sh` then curls the name right away; if that first lookup gets "no answer", resolvers cache the miss for the zone's negative-cache time. Route 53's default SOA is `... 900 1209600 86400` with a 900 s TTL, so a miss can be cached up to **15 minutes**, longer than the script's 10-minute check. Fits both runs (failed in-script, fine ~15 min later). Testing while the cluster is up: compare the Mac's resolver with the authoritative nameserver, and read the zone's SOA.
  - **Check ~11 min after `up.sh` ended (screenshot 60b):** the Mac's resolver and the AWS nameserver now agree (new ALB IPs 34.199.40.143 / 34.205.192.221); HTTPS **200**; zone SOA confirmed `... 1 7200 900 1209600 86400` (SOA TTL 900 s, so a cached "no answer" can live up to **15 minutes**). Consistent with the hypothesis but **not proven**: by the time we looked, any cached miss had already expired. Both failing runs follow the same shape: fail in-script, fine ~10–15 min later.
  - Decision: fix both possible weak points rather than wait for a third failure to catch it live. (1) Terraform-managed SOA with negative-cache time 60 s (a cached "no" lasts 1 minute, not 15). (2) `up.sh` waits until the ALB itself reports `active` before the first lookup, and judges success by asking **AWS's own nameserver** and connecting to that IP (`curl --resolve`), so the result reflects Patrol, not my laptop's DNS cache; a normal-DNS check is printed for information only.
- Teardown with the DNS wait: 57 destroyed, cluster down in 13m 14s, all ok (screenshot 61). The DNS-wait lines scrolled above the screenshot; the zone listing is the real proof (pending).
- **✅ ExternalDNS cleans up after itself (screenshot 62).** After `down.sh`, the zone held only NS, SOA and the ACM validation CNAME: A, AAAA and both ownership TXT records were removed by ExternalDNS itself, before it was shut down. No console cleanup. The full DNS lifecycle now works unattended: create on rebuild → repoint owned records to a new ALB → delete on teardown.
- Still open before closing Phase 3: the end-of-`up.sh` "not answering" check (SOA negative-cache + authoritative check fix, awaiting approval).
- **Fix for the false "not answering" (approved):**
  - **SOA in Terraform** (`aws_route53_record.soa`, `allow_overwrite`): minimum TTL **86400 → 60**, everything else Route 53's default (primary name server from `aws_route53_zone.primary_name_server`). Per the AWS docs, negative caching = the lesser of the SOA minimum and the SOA record's TTL (900), so a premature "doesn't exist" now lasts **1 minute instead of 15**. Trade-off from the same docs: more negative-answer queries reach Route 53 (billed per query); negligible at this traffic.
  - **`up.sh` final check rewritten:** waits for the Ingress address, then for the **ALB to report `active`**, then for ExternalDNS's record to point at **this** ALB, then asks **the zone's own AWS nameserver** for the IP and connects with `curl --resolve`. Success no longer depends on this laptop's DNS cache. A normal-DNS check is then printed for information ("your machine sees it too" / "hasn't caught up yet"). A genuine failure now says it's not a DNS cache issue and where to look.
  - Timing split in two: "Argo CD + apps healthy" (measured when the Patrol app is Healthy) and "load balancer + DNS + HTTPS check", so the numbers measure what they claim. `dig` added to the tool check.
  - ShellCheck clean; Terraform reference check clean.
- SOA applied: plan exactly **1 to add, 0 to change, 0 to destroy**, record value `ns-852.awsdns-42.net. awsdns-hostmaster.amazon.com. 1 7200 900 1209600 60` (screenshot 63); applied, and `dig SOA` against AWS's nameserver now ends in **60** (screenshot 64). Also visible: the `certificate_status` output caught up from `PENDING_VALIDATION` to `ISSUED` (Terraform's stored copy was stale until this refresh; the real certificate had been issued for hours).
- Screenshot note: screenshot 64 shows full ARNs including the ACM certificate ID and the Slack secret's ARN suffix; not secrets, but crop to the last line for the write-up.
- **✅ Clean bring-up, no false alarm (screenshot 65).** Every stage reported in order: controller → Patrol healthy; ALB `patrol-882290397…: active`; ExternalDNS pointed `mypatrolapp.cloud` at that ALB; HTTPS checked via AWS's nameserver `ns-852.awsdns-42.net`; `Status page: https://mypatrolapp.cloud/` "(this machine's DNS sees it too)". Honest timings now: **Argo CD + apps healthy 1m 42s after nodes Ready**; **load balancer + DNS + HTTPS check 1m 33s**; **everything up in 17m 20s** (the previous "11–12 min" figures were the false-alarm timeout, not Argo CD).
- Closing teardown: 57 destroyed, cluster down in 13m 32s, every leftover check ok (screenshots 66a, 66). Zone check (screenshot 67) pending to formally close Phase 3.
- Zone after the closing teardown: NS, SOA, ACM CNAME only (screenshot 67). Second automatic clean-up in a row.

**Phase 3 complete.** Patrol runs in EKS, installed entirely from Git by Argo CD (app of apps: load balancer controller v3.6.2, ExternalDNS v0.23.0, Patrol chart 0.2.0), at **https://mypatrolapp.cloud** (ACM certificate, TLS 1.2/1.3, HTTP→HTTPS redirect, /metrics blocked at the ALB). Every pod uses its own Pod Identity role, locked to its namespace + ServiceAccount. One `up.sh` builds everything in ~17 minutes (Argo CD + apps healthy ~1m 42s after nodes Ready); one `down.sh` removes everything, including load balancers and DNS records, in ~13 minutes, with nothing left over. Bugs found and fixed along the way: readiness gates on first pods, zone spread mid-rollout, base-image drift (digest pin), teardown order (orphaned ALB risk), ExternalDNS apex ownership (upstream #4234), teardown DNS race, negative DNS caching false alarm.

## Phase 4: CI/CD with GitHub Actions (planning)

- Decision: **the deploy step opens a pull request** with the `image.tag` change, and I merge it. Rejected the bot committing straight to `main` (faster, true continuous delivery, but no human checkpoint before a release). Trade-off accepted: one extra click per release, in exchange for a reviewed, approved deploy record in GitHub's PR history.

---

## Phase 4: CI/CD with GitHub Actions

**Plan approved.** Decision (mine): **the release bot opens a PR with the new image tag and I merge it** (a human approval gate before every deploy). Rejected the bot committing straight to `main` (true continuous delivery, but no review step). `main` left unprotected for now: PRs opened with GitHub's built-in token don't trigger workflows, so a "checks must pass" rule would block every deploy PR; revisit later.

**`ci.yml`** (every PR + push to main, `contents: read` only, no AWS access): pytest (Python 3.13); Helm 4.3.1 lint + render in both HTTPS and HTTP modes + kubeconform strict against Kubernetes 1.36 (kubeconform downloaded with checksum check) + guard-rail checks (wrong namespace / `tag=latest` must be refused); ShellCheck; `terraform fmt -check` + `validate` (no backend, no credentials) for bootstrap, permanent and cluster; build all three images and scan with Trivy (no push).
**`release.yml`** (push to main touching `app/**` or `config/**`, or manual): per image, build → **Trivy gate before anything is pushed** (HIGH/CRITICAL with a fix available, `--ignore-unfixed`, the rule set in Phase 3) → OIDC credentials → push to ECR as the 7-character commit (same as `push-images.sh`; skipped if the tag exists, since tags are immutable). Then `deploy-pr` sets `image.tag` and opens "Deploy images <tag>" via peter-evans/create-pull-request. `fail-fast` so one failing image stops the whole release (no partial deploys); `concurrency` so releases never overlap or get cancelled halfway. The deploy PR only changes `values.yaml`, which isn't in the release paths, so merging it can't trigger another release (no loop).

**AWS access via OIDC** (`infra/permanent/github-oidc.tf`): GitHub OIDC provider + role `patrol-github-actions`, 1-hour sessions. Trust: `aud = sts.amazonaws.com` and **`sub` matched exactly** to `repo:stevenwaldron@<owner_id>/patrol@<repo_id>:ref:refs/heads/main`. Researched first: repos created after 2026-07-15 get GitHub's **immutable subject format** with numeric IDs (GitHub changelog 2026-04-23); the old `repo:owner/repo:...` pattern would silently never match. Exact match, no wildcards: a renamed or re-created repo gets new IDs and can't inherit the trust; PRs and other branches get nothing. Permissions: `ecr:GetAuthorizationToken` (can't be scoped) + push/read actions on the 3 Patrol repositories only. No cluster access (planning decision: CI gets no EKS access entry), no state access.

**Supply chain:**
- Every third-party action pinned to a **full commit SHA** with a version comment (checkout v7.0.1, setup-python v7.1.0, setup-helm v5.0.1, setup-terraform v4.0.1, setup-buildx v4.4.1, build-push v7.4.0, configure-aws-credentials v6.3.0, amazon-ecr-login v2.1.7, create-pull-request v8.1.1, trivy-action v0.36.0). Versions and SHAs read from each repo's tags with `git ls-remote`.
- **Why it matters, found while choosing versions:** in **March 2026, 76 of trivy-action's 77 version tags were force-pushed to malicious commits that stole CI secrets** (GHSA-69fq-xp46-6x23). Verified before use: v0.36.0 (commit ed142fd, 2026-04-22) is a post-incident release on the official `master` branch, pins `setup-trivy` v0.2.6 by SHA (the advisory's safe version) and defaults to Trivy v0.70.0 (post-incident). Trivy version also pinned explicitly in the workflows.
- `persist-credentials: false` on checkouts that never push; job-level permissions (`id-token: write` only on the publish job).
- **Dependabot**: weekly grouped PRs for action SHAs, the Python base image digest and pip pins.
- Decision: base image changed from `ARG PYTHON_IMAGE=…` to **one literal `FROM …@sha256:… AS python`** that both stages build on. Couldn't confirm Dependabot reads ARG-based FROM lines; a literal line removes the doubt. Same digest, same image; the ARG only existed for workspace testing.

**Found while verifying (before anything ran):**
- **Bug (mine):** the guard-rail step used `! helm template …`. Under bash `-e` (how GitHub runs steps) a `!`-prefixed command never fails the step, so the check could never fail. Rewritten as explicit `if … then exit 1`. Proven both ways locally (bad input refused, good input renders).
- **Two existing files failed `terraform fmt -check`** (cluster `main.tf`, the VPC module's `main.tf`): alignment only. Would have turned CI red on its first run. Fixed with `terraform fmt` (whitespace only).
- Verification: actionlint 1.7.12 (checksum verified, with ShellCheck on every `run:` block) clean; CI steps emulated locally (helm lint, 24/24 kubeconform-valid across both modes, guards, ShellCheck, 30 tests, the release `sed` changes exactly the tag line); Terraform 1.17.1 fmt clean. `terraform validate` couldn't run here (provider registry blocked); covered by the plan on my Mac and by CI.
- Setup needed on GitHub: Settings → Actions → General → Workflow permissions → "Allow GitHub Actions to create and approve pull requests" (needed for the deploy PR). Order matters: apply the OIDC Terraform **before** pushing the workflows, since the push itself triggers the first release.

---

## Reminders (open items)

- [ ] **Hardening:** after first login, change the Argo CD admin password and delete `argocd-initial-admin-secret` (Argo CD's own install notes recommend it). Fits Phase 6 (security) or the Argo CD values.

- [x] **Phase 4:** Trivy gate (`HIGH,CRITICAL`, `--ignore-unfixed`, exit 1) and Dependabot for the base-image digest: built into ci.yml/release.yml/dependabot.yml.

- [ ] **After Phase 5:** update the GitHub repo description to mention Argo CD, Helm, Prometheus, Grafana and Slack alerts (e.g. "…deployed with Argo CD and Helm, monitored with Prometheus, Grafana and Slack alerts"), and add the topics `argocd`, `helm`, `prometheus`, `grafana`.
