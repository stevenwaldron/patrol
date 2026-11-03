"""Fail if a pod in a rendered chart needs a Secret or ConfigMap that nothing creates.

Such a pod never starts: it waits in ContainerCreating forever ("MountVolume.SetUp
failed ... secret not found"), and Argo CD reports the app Degraded. Found live in
Phase 5: turning off the operator's admission webhook removed the Secret holding
its certificate, but the operator still tried to mount it.

    python3 deploy/monitoring/check_references.py kps.yaml

Secrets created outside the chart (by External Secrets, from deploy/platform/secrets)
are listed in EXTERNAL.
"""
import sys, yaml
EXTERNAL = {"Secret": {"grafana-admin", "alertmanager-slack"}, "ConfigMap": set()}
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
have = {(d["kind"], d["metadata"]["name"]) for d in docs}
bad = []
for d in docs:
    if d["kind"] not in ("Deployment", "DaemonSet", "StatefulSet", "Job"): continue
    spec = d["spec"]["template"]["spec"]; refs = []
    for v in (spec.get("volumes") or []):
        if "secret" in v: refs.append(("Secret", v["secret"]["secretName"], v["secret"].get("optional")))
        if "configMap" in v: refs.append(("ConfigMap", v["configMap"]["name"], v["configMap"].get("optional")))
        for src in v.get("projected", {}).get("sources", []):
            if "secret" in src: refs.append(("Secret", src["secret"]["name"], src["secret"].get("optional")))
            if "configMap" in src: refs.append(("ConfigMap", src["configMap"]["name"], src["configMap"].get("optional")))
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for e in (c.get("env") or []):
            vf = e.get("valueFrom", {})
            if "secretKeyRef" in vf: refs.append(("Secret", vf["secretKeyRef"]["name"], vf["secretKeyRef"].get("optional")))
            if "configMapKeyRef" in vf: refs.append(("ConfigMap", vf["configMapKeyRef"]["name"], vf["configMapKeyRef"].get("optional")))
        for e in (c.get("envFrom") or []):
            for k, kind in (("secretRef", "Secret"), ("configMapRef", "ConfigMap")):
                if k in e: refs.append((kind, e[k]["name"], e[k].get("optional")))
    for kind, name, optional in refs:
        if optional or (kind, name) in have or name in EXTERNAL[kind]: continue
        bad.append(f'{d["kind"]}/{d["metadata"]["name"]} needs {kind} "{name}", which nothing creates')
print("\n".join(bad) or "every Secret/ConfigMap a pod needs is created by the chart or by External Secrets")
sys.exit(1 if bad else 0)
