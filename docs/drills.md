# Patrol failure drills (Phase 9)

Break one thing at a time, on purpose, and prove that Patrol **notices**, **alerts**
and **recovers**. For each drill: write the prediction *before* starting, run it,
then fill in what actually happened. A drill whose result disagrees with its
prediction is the most valuable kind: it shows something we didn't understand.

## Ground rules
- **One drill at a time.** Wait until everything is back to normal before the next.
- **Probe running for every drill** (its own terminal tab): `./infra/probe.sh`.
  Ctrl+C at the end of the drill prints failed requests and the longest outage.
- **Every change to the cluster goes through Git**, except where a drill is
  specifically about a manual change (drill 6) or about AWS itself (3, 4, 8).
- **Undo is written before the drill starts.** If something unexpected happens,
  undo first, investigate second.
- **End every session with `./infra/down.sh`**, even mid-drill.
- Times: note the clock time of the *break*, the first *alert* (Slack), and *recovery*.

Useful views (each in its own tab):
```bash
# Pods and nodes, refreshing every 5 s (macOS has no `watch`)
while true; do clear; date; kubectl --context patrol -n patrol get pods -o wide; \
  kubectl --context patrol get nodes -L karpenter.sh/capacity-type,topology.kubernetes.io/zone; sleep 5; done
# Argo CD apps
kubectl --context patrol -n argocd get applications
```
Plus Slack `#patrol-alerts`, and Grafana (port 3001): **Patrol: overview**.

---

## After any maintenance: rebalance the status page
Kubernetes only spreads pods when it places them, so after a drain or a node
replacement both status-page copies can stay on one node (Phase 9 drill 2), and
one more node failure would take the page down. **PatrolStatusPageNotSpread**
alerts after 15 minutes like that. Fix (a rolling restart: one copy at a time,
no downtime):
```bash
kubectl --context patrol -n patrol rollout restart deployment/status-page
```
*Why not automatic:* the Kubernetes descheduler would do this for us, but for a
cluster rebuilt every session it's a ninth platform component to run; the alert
makes the imbalance visible instead. In a long-lived cluster: add the descheduler
and keep the alert as its safety net.

---

## Session 1: losing machines
**Run order: 1 → 4 → 2 → 3.** Drills 2 and 3 take away a platform node, which
may also move Prometheus (no disk: its history restarts) and Grafana. Doing the
Spot drill first keeps its evidence intact.

### Drill 1: kill pods
| | |
|---|---|
| **Break** | `kubectl --context patrol -n patrol delete $(kubectl --context patrol -n patrol get pod -l app.kubernetes.io/component=status-page -o name \| head -1)` then the same with `component=worker` (`-o name` already prints `pod/<name>`, so no `pod` before it) |
| **Prediction** | Each Deployment replaces its pod within seconds. Probe: **0 failed** (the other status-page copy keeps serving; the load balancer stops sending to a pod as soon as it starts shutting down, thanks to the readiness gate and the preStop pause). No alerts. |
| **Undo** | Nothing: Kubernetes does it. |
| **Signals** | Pods view: a new pod name, `Running` within ~30 s. |

**Result (Nov 10):** status page pod deleted 00:30, replacement Running and Ready (readiness gate 1/1) within seconds; worker pod deleted 00:34, replacement Running within a minute on the same Spot node; scheduler runs kept completing every minute. Probe: **588 requests over 677 s, 0 failed (100.00%)**. No alerts. **Prediction confirmed.**

### Drill 4: AWS reclaims the Spot worker node (AWS FIS)
| | |
|---|---|
| **Break** | `cd infra/cluster && aws fis start-experiment --profile patrol --experiment-template-id "$(AWS_PROFILE=patrol terraform output -raw spot_interruption_experiment)"` (cost about $0.10) |
| **Prediction** | AWS sends a rebalance recommendation and a **2-minute** interruption notice. Karpenter reads it from its queue, launches a replacement Spot node and drains the old one before AWS takes it. Workers restart on the new node. Orders wait in SQS meanwhile (none lost). Check freshness may dip for a minute or two; **no PatrolChecksStale** (it needs 5 stale minutes). Probe: **0 failed** (the status page isn't on Spot). |
| **Undo** | Nothing: Karpenter does it. If no replacement appears within 5 minutes: `kubectl --context patrol -n kube-system logs deploy/karpenter \| grep -i -E 'interrupt\|error'`. |
| **Signals** | `kubectl --context patrol get nodeclaims -w` (a second NodeClaim appears, the first goes); Karpenter logs (`interruption`); Grafana "Karpenter: worker nodes" and "Age of latest check". FIS console: experiment `completed`. |

**Result (Nov 10):** FIS experiment started 00:41 (ID EXPaGRK8P53DBTMqKp). Karpenter log 00:41:08: `initiating delete from interruption message`, `messageKind: spot_interrupted`, queue `patrol-karpenter`, action `CordonAndDrain`; old node tainted `karpenter.sh/disrupted` the same second. **Replacement ordered at once:** `workers-f4w2r`, **t3a.small Spot in us-east-1b** (different type and zone from the reclaimed t3.small in 1a). Workers moved one by one (`wsm5l`, then `hb4fd`; the second eviction was "Forceful Termination", i.e. not held back by the workers' disruption budget with the node about to vanish). By 00:43:52: 1 NodeClaim (the new one), 2 workers Running. Grafana: Spot nodes 1 → **2** (make-before-break) → 1; workers never below 2. Scheduler kept running on the platform nodes throughout. Probe: **384 requests / 439 s, 0 failed (100.00%)**. No Slack alert. **Prediction confirmed.**

### Drill 2: planned maintenance (drain a platform node)
| | |
|---|---|
| **Break** | Pick the managed node running a status-page pod (pods view), then: `kubectl --context patrol drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=5m` |
| **Prediction** | Pods leave one at a time; the disruption budgets keep at least one status-page copy up. Probe: **0 failed**. **Honest risk:** the two t3.medium nodes are each ~45% full by requests, so one node can't hold everything: some platform pods (possibly Prometheus) will stay **Pending** until the node is uncordoned. While Prometheus is down, no alert can fire. |
| **Undo** | `kubectl --context patrol uncordon <node>` |
| **Signals** | Pods view; `kubectl --context patrol get pods -A --field-selector=status.phase=Pending`. |

**Result (Nov 10): prediction partly wrong — the most useful drill so far.** Drained `ip-10-20-22-207` at ~00:52 (completed); uncordoned 00:56.
- Probe: **116 requests / 149 s, 2 failed (98.28%)**: timeouts (`000`, 5 s) at **00:52:14 and 00:52:21**. Predicted 0.
- **Cause (evidence lines up):** the same drain evicted the status-page pod *and* the only AWS Load Balancer Controller (~00:52:03). The status page's preStop pause (10 s) ended ~00:52:13 and the process stopped, but with no controller running nobody removed it from the ALB's target group, so the ALB kept sending some requests to a dead target until the new controller became leader (~00:52:2x). Both failures fall inside that window.
- **Pending pods:** `scheduler` (**no new checks ordered** while it waited), `kyverno-admission-controller` (Kyverno down; fail-open as designed: Patrol's 5 rules unenforced, Pod Security Admission still enforcing), `kube-prometheus-stack-operator` (config only; Prometheus itself was on the other node, so alerts kept working). Predicted Prometheus might be stuck; it wasn't. Didn't predict the scheduler.
- Both status-page copies ended up on the same node: no zone spread until the node returned.
- **Alerting worked anyway:** the Pending scheduler runs hit their 50-second deadline and failed, so **PatrolSchedulerJobFailed** reached Slack at 00:54 (runs 29904833, -834, -835), ~2 minutes into the problem. PatrolChecksStale did not fire (checking paused ~4 minutes, under its 5 + 5). After uncordon: no Pending pods.
- **Findings to fix:** (1) correction: the controller already runs 2 replicas, but the drain evicted the *leader*, and the standby only took over when the lease expired (~20 s) → make the status-page pod outlive that gap (longer preStop pause); (2) two platform nodes can't absorb each other (no N+1 headroom) → a priority class so Patrol's scheduler and status page pre-empt less critical pods, and/or a third platform node.

**Re-run after the fix list (Nov 11, chart 0.7.0): fixes proven.** Same setup as the first run: drained `ip-10-20-28-226`, which held a status-page copy, the scheduler and the load balancer controller's **leader** (checked with the `aws-load-balancer-controller-leader` lease). Drained at ~09:12, uncordoned ~09:14.
- Probe: **191 requests / 221 s, 0 failed (100%)**. Was 2 failed. The 30 s preStop pause outlasted the leader handover.
- Scheduler: **not Pending**. Runs `29906773` and `29906774` completed on the other node (priority `patrol-critical`).
- **New finding A:** this time **Prometheus** was on the drained node and was the pod left Pending (~2–3 min, 400 Mi request didn't fit). No alert could fire in that window, and with no persistent volume its history was lost. The no-N+1-headroom problem moved, it didn't go away.
- **New finding B:** the drain evicted a running scheduler pod (`scheduler-29906772`). With `backoffLimit: 0` the eviction counted as a failed Job, so **PatrolSchedulerJobFailed** reached Slack at 09:15 (resolved 09:25). No checks were missed. Fix idea: a Job `podFailurePolicy` that ignores `DisruptionTarget`.
- Not-spread alert: went **Pending** correctly with both copies on `ip-10-20-43-101`. The rebalance was done before its 15 min ran out, so it never reached Slack (the Firing path is covered by the unit test). After `rollout restart` the copies were on different nodes and the rule was back to **inactive**.
- Teardown: 85 destroyed, nothing left over, 15m14s.

### Drill 3: a platform node dies suddenly
| | |
|---|---|
| **Break** | EC2 ID of a managed node: `kubectl --context patrol get node <node> -o jsonpath='{.spec.providerID}'` (the `i-…` part), then `aws ec2 terminate-instances --profile patrol --instance-ids i-…` |
| **Prediction** | No warning this time. The status-page copy on the other node keeps serving (they're spread across zones); probe: **a few failed requests at most**, while the load balancer notices the dead target. The managed node group launches a replacement in ~3–5 minutes; Pending pods start there. Prometheus may lose its history if it was on that node. Possible alerts: KubeNodeNotReady, target down. |
| **Undo** | Nothing: the node group replaces it. |
| **Signals** | Nodes view (`NotReady`, then a new node); probe. |

**Result (Nov 10): better than predicted, faster than predicted.** Run as option B: one status-page copy and the standby load balancer controller first moved to the other node (drill 2 had left both copies, and both controllers, on one node). Terminated `i-0714658518073c0e0` (`ip-10-20-35-68`, holding a status-page copy and the controller **leader**) at 01:11.
- Probe: **246 requests / 287 s, 1 failed (99.59%)**: a single **502 in 0.08 s at 01:11:43**. A fast 502 from the load balancer (a connection to a dead target refused), not a 5-second timeout.
- Replacement node `ip-10-20-36-247` Ready ~1 minute before 01:15 (~3 min after termination). The lost status-page copy came back in ~1½ minutes (`v244v`), on the surviving node, not after the ~5 minutes I expected: EKS noticed the instance was terminated and deleted the Node object, so its pods were replaced at once instead of waiting out the unreachable-node timeout. No Pending pods at the end.
- Predicted "a few failures over 20–40 s" and ~5-minute pod replacement; reality was 1 failure and ~1½ minutes.

---

## Session 2: breaking things in software

### Drill 5: a monitored site goes down (via Git)
| | |
|---|---|
| **Break** | In `deploy/charts/patrol/values.yaml`, set `drill.siteUrlOverrides: { chess: https://patrol-drill.invalid }`; commit "Drill 5: chess down"; push. (Only the chart changes: `sites` and `config/` stay untouched, so no image release is triggered and the sites test still passes.) |
| **Prediction** | Argo CD syncs within ~2–3 min; the status page and scheduler pick up the new URL; chess checks fail; the status page shows chess DOWN; **PatrolSiteDown** reaches Slack about 3–4 minutes after the first failed check. |
| **Undo** | `git revert HEAD && git push` → **RESOLVED** in Slack. |
| **Signals** | Status page; Grafana "Site status"; Slack. Times: push → Argo CD synced → first failed check → Slack. |

**Result (Nov 11), break half:** commit `b571b0a` "Drill 5: chess down" at 23:58:17. Status page: chess **Down**, URL `https://patrol-drill.invalid`, `ConnectError: [Errno -2] Name or service not known`; the timeline shows 3, later 8, red checks after a green history. **PatrolSiteDown (chess)** in Slack at **00:07**, ~9 minutes after the commit. Predicted 6–8 minutes: within a minute or so (Argo CD's poll interval plus the 3-minute `for`). The intentionally broken site still alerted nothing, as designed.

**Undo result:** `git revert` pushed; chess back to **Operational** on its real CloudFront URL (latency ~60 ms); its timeline shows the run of red checks between green ones (24-hour uptime 84.52%). Whole round trip, break and fix, done only through Git. **RESOLVED** (green ✅ PatrolSiteDown (chess)) in Slack at **00:17**, ~4 minutes after the revert at 00:13:12. Detection: commit → alert ~9 min; fix → resolved ~4 min.

### Drill 6: someone "fixes" things by hand
| | |
|---|---|
| **Break** | `kubectl --context patrol -n patrol scale deployment status-page --replicas=1` |
| **Prediction** | Argo CD sees the cluster no longer matches Git and **self-heals** back to 2 replicas within about 3 minutes (or immediately after a Refresh). The lesson: in GitOps, a manual change is temporary; the fix belongs in Git. |
| **Undo** | Nothing: Argo CD does it. |
| **Signals** | `kubectl --context patrol -n patrol get deploy status-page -w`; Argo CD UI (patrol app OutOfSync, then Synced). |

**Result (Nov 11): as predicted.** `kubectl scale deployment status-page --replicas=1` at ~00:35. The Deployment went 2/2 → 2/1 → **1/1** at once (Kubernetes obeyed), then the desired count snapped back to **1/2** — Argo CD had already noticed the drift and re-applied Git's 2 — and a new pod started: **2/2** within about a minute (deployment age 44m → 45m during the whole episode). Faster than the ~3 minutes predicted: Argo CD reacts to changes in objects it manages, not only on its 2-minute Git poll. One copy kept serving throughout; no alerts.

### Drill 7: checking stops (scheduler paused via Git)
| | |
|---|---|
| **Break** | `drill.schedulerSuspended: true` in `values.yaml`; commit "Drill 7: scheduler paused"; push. |
| **Prediction** | No new orders. Ages pass 3 minutes (freshness SLI drops), then 5 minutes: **PatrolChecksStale** (critical) after a further 5 minutes, i.e. roughly 10 minutes after the last run. If the cluster has been up over an hour, the freshness **fast-burn** alert may fire too. |
| **Undo** | `git revert HEAD && git push` → RESOLVED. |
| **Signals** | Grafana "Age of latest check" and the SLO dashboard; Slack. |

**Result (Nov 11): as predicted, plus one bonus alert.** Commit `0051384` "Drill 7: scheduler paused" at 00:46:47; Argo CD applied it ~2 min later (CronJob `suspend: true`, last run 00:49). Check ages climbed ~1 minute per minute in 30-second steps (status-page cache + scrape interval): past the 3-minute SLO line ~00:53, past 5 minutes ~00:55. **At 01:00 Slack received PatrolChecksStale (critical) for all three sites**, "latest check is 10m 7s old" (predicted 01:00–01:01), **and PatrolCheckFreshnessFastBurn** ("stale share over 1h is 18.33%, SLO allows 1%"): the cluster had been up over an hour, so the full-window guard from Phase 5 allowed the burn alert, as the runbook said it might. Note: PatrolChecksStale fires for broken-test too — staleness means "not being checked", which is true for every site; decision A (no alert for broken-test) applies to PatrolSiteDown only. Ages reached ~23 min before the revert.

**Undo result:** `git revert` pushed; ages peaked at **~30 min** and dropped to seconds at **~01:21** (the first scheduler run after Argo CD unpaused it). **RESOLVED for PatrolChecksStale × 3 at 01:25** ("30m 7s old" = the last value while firing). The fast-burn alert cleared more slowly by design (1-hour window): its RESOLVED arrived ~01:36, reading "46.67%", its last value while firing.

### Drill 8: the autoscaler loses its AWS access
| | |
|---|---|
| **Break** | `aws iam detach-role-policy --profile patrol --role-name patrol-keda --policy-arn arn:aws:iam::419701112481:policy/patrol-keda` |
| **Prediction** | KEDA's queue reads fail; after 3 failures the **fallback** holds 2 workers; the ScaledObject reports fallback, so Argo CD marks the patrol app **Degraded**; **PatrolAutoscalerErrors** reaches Slack after ~5–6 minutes. Checks continue (2 workers are enough at rest). |
| **Undo** | Through Terraform, which also shows drift detection: `cd infra/permanent && AWS_PROFILE=patrol terraform plan` (expect **1 to add**: the missing attachment) then `apply`. |
| **Signals** | `kubectl --context patrol -n patrol get scaledobject` (FALLBACK True); KEDA operator logs; Argo CD; Slack. |

**Result (Nov 11), break half: as predicted.** `aws iam detach-role-policy … patrol-keda` at ~01:34:40. KEDA's first failed read at **06:35:09 UTC (01:35:09)**, ~30 s later: `GetQueueAttributes … StatusCode: 403 … AccessDenied: User: …assumed-role/patrol-keda/eks-patrol-keda-opera-… is not authorized to perform: sqs:getqueueattributes on resource: …:patrol-checks because no identity-based policy allows the sqs:getqueueattributes action` — the exact role (via Pod Identity), action and queue, i.e. least privilege visible from the denied side. By **01:36:26**: ScaledObject **FALLBACK True** (workers held at 2), and Argo CD's patrol app **Synced / Degraded** (its built-in KEDA health check treats fallback as unhealthy). The status page and checking were unaffected. Slack: PatrolAutoscalerErrors _(time)_.

**Undo result:** `terraform plan` in the permanent layer detected the drift on its own: **`Plan: 1 to add, 0 to change, 0 to destroy`** (the missing role-policy attachment); `terraform apply` restored it ~01:42. Recovery by 01:43: FALLBACK False, patrol app Synced / **Healthy**. **PatrolAutoscalerErrors** reached Slack at **01:40** (~5½ min after the first denied read; predicted 5–6). Its text said "2.012 failed queue reads in the last 10 minutes" while KEDA's log showed several errors every few seconds: a wording finding (see running log). RESOLVED at 01:45 ("1.053").

### Drill 9: a bad deploy
| | |
|---|---|
| **Break** | Set `image.tag: "0000000"` (a tag that doesn't exist in ECR; it passes Kyverno, which only checks the format) in `values.yaml`; commit "Drill 9: bad image tag"; push. |
| **Prediction** | The status page and workers roll out one new pod each, which can't pull its image (`ImagePullBackOff`); **the old pods keep serving** (rolling updates never remove a working copy before a new one is ready), so probe: **0 failed**. **But the scheduler has no "old version":** every new run uses the new tag, can't start, and is killed by its 50-second deadline, so **PatrolSchedulerJobFailed** fires, checks stop, and **PatrolChecksStale** follows. A real finding about CronJobs. |
| **Undo** | `git revert HEAD && git push`. |
| **Signals** | Pods view; `kubectl --context patrol -n patrol get jobs`; Slack. |

**Result (Nov 11): as predicted, plus a CI gap.** Commit `8743ee6` "Drill 9: bad image tag" (`0000000`) at 01:46:33. After Argo CD synced (~01:49): one new status-page pod and one new worker in **ImagePullBackOff**, the old pods **still Running** (74–121 min old); patrol app **Synced / Progressing**. Scheduler: the runs created before the sync completed; from the first run after it, every run was stuck in ImagePullBackOff and killed by its 50 s deadline → **PatrolSchedulerJobFailed at 01:52** (~6 min after the push). Probe across the whole drill: **565 requests / 648 s, 0 failed (100.00%)**. Reverted (`5afce79`) ~01:57.
- *Why the two behaved differently:* Deployments roll out a new copy before removing an old one (`maxUnavailable: 0`), so a version that can't start never replaces a working one. A CronJob has no "old version": each run is created fresh from the current spec, so a bad image stops checking outright.
- **CI gap:** CI was **green** on the bad-tag commit. It validates the chart and the policies (`0000000` is a valid commit-ID format), but never checks that the tag exists in ECR. Fix: a CI step that runs `aws ecr describe-images` for the chart's tag (CI already has an OIDC role for ECR) and fails if it's missing.

### Drill 10: a policy violation in a pull request (no cluster needed)
| | |
|---|---|
| **Break** | `git switch -c drill/writable-fs`; in `deploy/charts/patrol/templates/_helpers.tpl` change `readOnlyRootFilesystem: true` to `false`; commit; `git push -u origin drill/writable-fs`; open a PR on GitHub. |
| **Prediction** | CI's **policies** job fails (`patrol-read-only-root-fs`), and the ruleset makes **Merge** unavailable. The bad change never reaches Argo CD. |
| **Undo** | Close the PR, delete the branch (GitHub button), then locally: `git switch main && git branch -D drill/writable-fs`. |
| **Signals** | The PR's checks; the failing step's message. |

**Result (Nov 11): as predicted.** Branch `drill/writable-fs` (commit `f96c4ff`, one line in `_helpers.tpl`), PR opened ~02:03. **1 failing, 10 successful checks:** `ci / policies` failed after 16 s, every other required check green — the change is valid YAML, a valid chart, a buildable image; only the policy layer objects. **Merge pull request is greyed out** ("Required" on all 11 checks). The bad change never reached `main`, Argo CD or the cluster.
- *Honest caveat:* as repository admin (the bypass list), Steven still sees **"Merge without waiting for requirements to be met (bypass rules)"**. The ruleset protects against bots, Dependabot and mistakes, not against the admin deliberately overriding it. Trade-off accepted for a one-person repo (direct pushes to `main` need it); a team would remove the bypass and require PRs.
