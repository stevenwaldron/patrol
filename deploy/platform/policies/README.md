# Patrol's own admission rules (Kyverno ValidatingPolicy, CEL)

Applied by Argo CD (`deploy/argocd/apps/patrol-policies.yaml`, wave 1) to pods in
the `patrol` namespace, and to the Deployments, CronJobs and Jobs that create them
(Kyverno writes those variants automatically: "autogen").

| Policy | Rule |
|---|---|
| patrol-allowed-images | images only from Patrol's ECR repositories |
| patrol-pinned-images | image tag is a git commit ID (or the image is pinned by digest) |
| patrol-read-only-root-fs | every container's root filesystem is read-only |
| patrol-require-resources | every container sets a memory limit and a CPU request |
| patrol-no-token-automount | no Kubernetes API token mounted into the pod |

Pod Security Admission (built into Kubernetes) enforces the "restricted" standard on
the same namespace; these rules cover what it can't express.

`validationActions`: ran in **Audit** first (results in PolicyReports, nothing blocked),
now **Deny + Audit** (switched Nov 6, after the live reports showed Patrol passing all 22 policies).

Tests: `kyverno test deploy/platform/policies/tests` (good and bad pods per rule),
and CI runs every policy against the rendered Patrol chart.
