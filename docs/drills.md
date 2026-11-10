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

## Session 1: losing machines
**Run order: 1 → 4 → 2 → 3.** Drills 2 and 3 take away a platform node, which
may also move Prometheus (no disk: its history restarts) and Grafana. Doing the
Spot drill first keeps its evidence intact.

### Drill 1: kill pods
| | |
|---|---|
| **Break** | `kubectl --context patrol -n patrol delete pod $(kubectl --context patrol -n patrol get pod -l app.kubernetes.io/component=status-page -o name \| head -1)` then the same with `component=worker` |
| **Prediction** | Each Deployment replaces its pod within seconds. Probe: **0 failed** (the other status-page copy keeps serving; the load balancer stops sending to a pod as soon as it starts shutting down, thanks to the readiness gate and the preStop pause). No alerts. |
| **Undo** | Nothing: Kubernetes does it. |
| **Signals** | Pods view: a new pod name, `Running` within ~30 s. |

**Result:** _break at …, recovered at …, probe: … failed, longest outage … s. Alerts: …_

### Drill 4: AWS reclaims the Spot worker node (AWS FIS)
| | |
|---|---|
| **Break** | `cd infra/cluster && aws fis start-experiment --profile patrol --experiment-template-id "$(AWS_PROFILE=patrol terraform output -raw spot_interruption_experiment)"` (cost about $0.10) |
| **Prediction** | AWS sends a rebalance recommendation and a **2-minute** interruption notice. Karpenter reads it from its queue, launches a replacement Spot node and drains the old one before AWS takes it. Workers restart on the new node. Orders wait in SQS meanwhile (none lost). Check freshness may dip for a minute or two; **no PatrolChecksStale** (it needs 5 stale minutes). Probe: **0 failed** (the status page isn't on Spot). |
| **Undo** | Nothing: Karpenter does it. If no replacement appears within 5 minutes: `kubectl --context patrol -n kube-system logs deploy/karpenter \| grep -i -E 'interrupt\|error'`. |
| **Signals** | `kubectl --context patrol get nodeclaims -w` (a second NodeClaim appears, the first goes); Karpenter logs (`interruption`); Grafana "Karpenter: worker nodes" and "Age of latest check". FIS console: experiment `completed`. |

**Result:** _…_

### Drill 2: planned maintenance (drain a platform node)
| | |
|---|---|
| **Break** | Pick the managed node running a status-page pod (pods view), then: `kubectl --context patrol drain <node> --ignore-daemonsets --delete-emptydir-data --timeout=5m` |
| **Prediction** | Pods leave one at a time; the disruption budgets keep at least one status-page copy up. Probe: **0 failed**. **Honest risk:** the two t3.medium nodes are each ~45% full by requests, so one node can't hold everything: some platform pods (possibly Prometheus) will stay **Pending** until the node is uncordoned. While Prometheus is down, no alert can fire. |
| **Undo** | `kubectl --context patrol uncordon <node>` |
| **Signals** | Pods view; `kubectl --context patrol get pods -A --field-selector=status.phase=Pending`. |

**Result:** _…_

### Drill 3: a platform node dies suddenly
| | |
|---|---|
| **Break** | EC2 ID of a managed node: `kubectl --context patrol get node <node> -o jsonpath='{.spec.providerID}'` (the `i-…` part), then `aws ec2 terminate-instances --profile patrol --instance-ids i-…` |
| **Prediction** | No warning this time. The status-page copy on the other node keeps serving (they're spread across zones); probe: **a few failed requests at most**, while the load balancer notices the dead target. The managed node group launches a replacement in ~3–5 minutes; Pending pods start there. Prometheus may lose its history if it was on that node. Possible alerts: KubeNodeNotReady, target down. |
| **Undo** | Nothing: the node group replaces it. |
| **Signals** | Nodes view (`NotReady`, then a new node); probe. |

**Result:** _…_

---

## Session 2: breaking things in software

### Drill 5: a monitored site goes down (via Git)
| | |
|---|---|
| **Break** | In `deploy/charts/patrol/values.yaml`, set `drill.siteUrlOverrides: { chess: https://patrol-drill.invalid }`; commit "Drill 5: chess down"; push. (Only the chart changes: `sites` and `config/` stay untouched, so no image release is triggered and the sites test still passes.) |
| **Prediction** | Argo CD syncs within ~2–3 min; the status page and scheduler pick up the new URL; chess checks fail; the status page shows chess DOWN; **PatrolSiteDown** reaches Slack about 3–4 minutes after the first failed check. |
| **Undo** | `git revert HEAD && git push` → **RESOLVED** in Slack. |
| **Signals** | Status page; Grafana "Site status"; Slack. Times: push → Argo CD synced → first failed check → Slack. |

**Result:** _…_

### Drill 6: someone "fixes" things by hand
| | |
|---|---|
| **Break** | `kubectl --context patrol -n patrol scale deployment status-page --replicas=1` |
| **Prediction** | Argo CD sees the cluster no longer matches Git and **self-heals** back to 2 replicas within about 3 minutes (or immediately after a Refresh). The lesson: in GitOps, a manual change is temporary; the fix belongs in Git. |
| **Undo** | Nothing: Argo CD does it. |
| **Signals** | `kubectl --context patrol -n patrol get deploy status-page -w`; Argo CD UI (patrol app OutOfSync, then Synced). |

**Result:** _…_

### Drill 7: checking stops (scheduler paused via Git)
| | |
|---|---|
| **Break** | `drill.schedulerSuspended: true` in `values.yaml`; commit "Drill 7: scheduler paused"; push. |
| **Prediction** | No new orders. Ages pass 3 minutes (freshness SLI drops), then 5 minutes: **PatrolChecksStale** (critical) after a further 5 minutes, i.e. roughly 10 minutes after the last run. If the cluster has been up over an hour, the freshness **fast-burn** alert may fire too. |
| **Undo** | `git revert HEAD && git push` → RESOLVED. |
| **Signals** | Grafana "Age of latest check" and the SLO dashboard; Slack. |

**Result:** _…_

### Drill 8: the autoscaler loses its AWS access
| | |
|---|---|
| **Break** | `aws iam detach-role-policy --profile patrol --role-name patrol-keda --policy-arn arn:aws:iam::419701112481:policy/patrol-keda` |
| **Prediction** | KEDA's queue reads fail; after 3 failures the **fallback** holds 2 workers; the ScaledObject reports fallback, so Argo CD marks the patrol app **Degraded**; **PatrolAutoscalerErrors** reaches Slack after ~5–6 minutes. Checks continue (2 workers are enough at rest). |
| **Undo** | Through Terraform, which also shows drift detection: `cd infra/permanent && AWS_PROFILE=patrol terraform plan` (expect **1 to add**: the missing attachment) then `apply`. |
| **Signals** | `kubectl --context patrol -n patrol get scaledobject` (FALLBACK True); KEDA operator logs; Argo CD; Slack. |

**Result:** _…_

### Drill 9: a bad deploy
| | |
|---|---|
| **Break** | Set `image.tag: "0000000"` (a tag that doesn't exist in ECR; it passes Kyverno, which only checks the format) in `values.yaml`; commit "Drill 9: bad image tag"; push. |
| **Prediction** | The status page and workers roll out one new pod each, which can't pull its image (`ImagePullBackOff`); **the old pods keep serving** (rolling updates never remove a working copy before a new one is ready), so probe: **0 failed**. **But the scheduler has no "old version":** every new run uses the new tag, can't start, and is killed by its 50-second deadline, so **PatrolSchedulerJobFailed** fires, checks stop, and **PatrolChecksStale** follows. A real finding about CronJobs. |
| **Undo** | `git revert HEAD && git push`. |
| **Signals** | Pods view; `kubectl --context patrol -n patrol get jobs`; Slack. |

**Result:** _…_

### Drill 10: a policy violation in a pull request (no cluster needed)
| | |
|---|---|
| **Break** | `git switch -c drill/writable-fs`; in `deploy/charts/patrol/templates/_helpers.tpl` change `readOnlyRootFilesystem: true` to `false`; commit; `git push -u origin drill/writable-fs`; open a PR on GitHub. |
| **Prediction** | CI's **policies** job fails (`patrol-read-only-root-fs`), and the ruleset makes **Merge** unavailable. The bad change never reaches Argo CD. |
| **Undo** | Close the PR, delete the branch (GitHub button), then locally: `git switch main && git branch -D drill/writable-fs`. |
| **Signals** | The PR's checks; the failing step's message. |

**Result:** _…_
