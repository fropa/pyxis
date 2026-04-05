"""
Install script generator.
Returns pre-configured install commands/scripts for each platform.
The API key and API URL are embedded so the customer gets a true one-liner.

Endpoints (no auth — these are public by design, the key is in the URL):
  GET /install/linux          → shell script (pipe to bash)
  GET /install/k8s-manifest   → kubectl-apply-ready YAML
  GET /install/helm-values    → Helm values.yaml
  GET /install/docker         → docker run one-liner (plain text)
  GET /install/shipper.py     → raw shipper script (fetched by install.sh)
"""
from pathlib import Path

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse, Response

router = APIRouter()

_AGENT_DIR = Path(__file__).parent.parent.parent.parent.parent / "agent"


def _read_agent_file(name: str) -> str:
    path = _AGENT_DIR / name
    if path.exists():
        return path.read_text()
    return f"# {name} not found"


# ── Linux shell script ────────────────────────────────────────────────────────

@router.get("/linux", response_class=PlainTextResponse)
async def install_linux(
    api_key: str = Query(..., description="Tenant API key"),
    api_url: str = Query(..., description="InfraWatch backend URL"),
    sources: str = Query("syslog", description="Comma-separated sources: syslog,k8s"),
):
    script = _read_agent_file("install.sh")
    script = script.replace("__API_KEY__", api_key)
    script = script.replace("__API_URL__", api_url.rstrip("/"))
    script = script.replace("syslog}", f"{sources}}}") if sources != "syslog" else script
    return PlainTextResponse(script, media_type="text/x-shellscript")


# ── Raw shipper.py (fetched by install.sh) ────────────────────────────────────

@router.get("/shipper.py", response_class=PlainTextResponse)
async def get_shipper():
    return PlainTextResponse(_read_agent_file("shipper.py"), media_type="text/x-python")


# ── Kubernetes manifest ───────────────────────────────────────────────────────

@router.get("/k8s-manifest", response_class=PlainTextResponse)
async def install_k8s(
    api_key: str = Query(...),
    api_url: str = Query(...),
    namespace: str = Query("monitoring"),
    sources: str = Query("k8s,syslog"),
):
    shipper_code = _read_agent_file("shipper.py")
    manifest = _K8S_MANIFEST_TEMPLATE.format(
        api_key=api_key,
        api_url=api_url.rstrip("/"),
        namespace=namespace,
        sources=sources,
        shipper_code=_indent(shipper_code, 4),
    )
    return PlainTextResponse(manifest, media_type="text/yaml")


# ── Helm values ───────────────────────────────────────────────────────────────

@router.get("/helm-values", response_class=PlainTextResponse)
async def install_helm(
    api_key: str = Query(...),
    api_url: str = Query(...),
    namespace: str = Query("monitoring"),
    sources: str = Query("k8s,syslog"),
):
    values = _HELM_VALUES_TEMPLATE.format(
        api_key=api_key,
        api_url=api_url.rstrip("/"),
        namespace=namespace,
        sources=sources,
    )
    return PlainTextResponse(values, media_type="text/yaml")


# ── Docker run ────────────────────────────────────────────────────────────────

@router.get("/docker", response_class=PlainTextResponse)
async def install_docker(
    api_key: str = Query(...),
    api_url: str = Query(...),
    sources: str = Query("syslog"),
):
    cmd = (
        f"docker run -d --name infrawatch-agent --restart=always \\\n"
        f"  -e INFRAWATCH_API_KEY={api_key} \\\n"
        f"  -e INFRAWATCH_API_URL={api_url.rstrip('/')} \\\n"
        f"  -e INFRAWATCH_SOURCES={sources} \\\n"
        f"  -v /var/log:/var/log:ro \\\n"
        f"  -v infrawatch-buffer:/var/lib/infrawatch/buffer \\\n"
        f"  infrawatch/agent:latest"
    )
    return PlainTextResponse(cmd)


# ── Templates ─────────────────────────────────────────────────────────────────

def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


_K8S_MANIFEST_TEMPLATE = """\
# InfraWatch agent — generated manifest
# Apply with: kubectl apply -f <this-file>
---
apiVersion: v1
kind: Namespace
metadata:
  name: {namespace}
---
apiVersion: v1
kind: Secret
metadata:
  name: infrawatch-secret
  namespace: {namespace}
type: Opaque
stringData:
  api-key: "{api_key}"
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: infrawatch-shipper
  namespace: {namespace}
data:
  shipper.py: |
{shipper_code}
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: infrawatch-agent
  namespace: {namespace}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: infrawatch-agent
rules:
  - apiGroups: [""]
    resources: ["events", "nodes", "pods", "namespaces"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: infrawatch-agent
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: infrawatch-agent
subjects:
  - kind: ServiceAccount
    name: infrawatch-agent
    namespace: {namespace}
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: infrawatch-agent
  namespace: {namespace}
  labels:
    app: infrawatch-agent
spec:
  selector:
    matchLabels:
      app: infrawatch-agent
  template:
    metadata:
      labels:
        app: infrawatch-agent
    spec:
      serviceAccountName: infrawatch-agent
      tolerations:
        - operator: Exists          # run on all nodes including masters
      containers:
        - name: agent
          image: python:3.12-slim
          command: ["python3", "/agent/shipper.py", "--sources", "{sources}"]
          env:
            - name: INFRAWATCH_API_KEY
              valueFrom:
                secretKeyRef:
                  name: infrawatch-secret
                  key: api-key
            - name: INFRAWATCH_API_URL
              value: "{api_url}"
            - name: INFRAWATCH_NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
            - name: INFRAWATCH_NODE_KIND
              value: "k8s_node"
            - name: INFRAWATCH_BUFFER_DIR
              value: "/var/lib/infrawatch/buffer"
          volumeMounts:
            - name: shipper
              mountPath: /agent
            - name: varlog
              mountPath: /var/log
              readOnly: true
            - name: buffer
              mountPath: /var/lib/infrawatch/buffer
          resources:
            requests:
              cpu: "50m"
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "128Mi"
      volumes:
        - name: shipper
          configMap:
            name: infrawatch-shipper
        - name: varlog
          hostPath:
            path: /var/log
        - name: buffer
          emptyDir: {{}}
"""

_HELM_VALUES_TEMPLATE = """\
# InfraWatch agent Helm values — generated by portal
# Install:
#   helm repo add infrawatch https://charts.infrawatch.io
#   helm install infrawatch-agent infrawatch/agent -f <this-file> -n {namespace} --create-namespace

agent:
  apiKey: "{api_key}"
  apiUrl: "{api_url}"
  sources: "{sources}"

  # Which nodes to run on (empty = all nodes)
  nodeSelector: {{}}
  tolerations:
    - operator: Exists

  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 200m
      memory: 128Mi

  buffer:
    enabled: true
    size: 100Mi

rbac:
  create: true

namespace: {namespace}
"""
