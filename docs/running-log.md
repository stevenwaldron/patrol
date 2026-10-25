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
