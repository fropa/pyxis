"""
Known failure patterns library.

A curated map of failure signatures → diagnostic context injected into
Claude's prompt. This makes RCA precise and actionable even without
any customer IaC indexed (starter tier).

Claude already knows what CrashLoopBackOff means generically.
These patterns give it the *specific diagnostic questions and checks*
relevant to each failure type so it doesn't give generic answers.
"""
from dataclasses import dataclass, field


@dataclass
class Pattern:
    name: str
    signals: list[str]                  # substrings to match (case-insensitive)
    severity: str                       # low | medium | high | critical
    context: str                        # injected into Claude prompt as context
    quick_checks: list[str] = field(default_factory=list)  # shown in frontend too


PATTERNS: list[Pattern] = [

    # ── K8s image problems ────────────────────────────────────────────────────

    Pattern(
        name="ImagePullBackOff",
        signals=["ImagePullBackOff", "ErrImagePull", "Back-off pulling image", "failed to pull"],
        severity="high",
        context="""
The container image cannot be pulled. This is one of the most common silent deploy failures —
the pipeline shows success but the pod never starts.

Diagnostic tree:
1. Does the image tag actually exist in the registry?
   → Check: `docker manifest inspect <image>:<tag>` or registry UI
2. Are the registry credentials valid and not expired?
   → Check: imagePullSecrets in the pod spec / deployment
   → Verify the secret exists: `kubectl get secret <name> -n <namespace>`
   → Verify it's of type kubernetes.io/dockerconfigjson
3. Is the registry reachable from the node?
   → Network policy, firewall, VPN, or private registry without node access
4. Is the image name/tag spelled correctly (including case)?
5. For ECR/GCR/ACR: are the node's IAM permissions / workload identity correct?
""",
        quick_checks=[
            "Check image tag exists in registry",
            "Verify imagePullSecrets secret exists in the correct namespace",
            "Check secret type is kubernetes.io/dockerconfigjson",
            "Test registry connectivity from affected node",
        ],
    ),

    # ── K8s pod crashes ───────────────────────────────────────────────────────

    Pattern(
        name="CrashLoopBackOff",
        signals=["CrashLoopBackOff", "crash loop", "back-off restarting failed container"],
        severity="high",
        context="""
The container starts and immediately exits. Kubernetes restarts it with exponential backoff.
The pipeline may show success — this failure is only visible in K8s events and pod logs.

Diagnostic tree:
1. Get the actual error: `kubectl logs <pod> --previous` (logs from the crashed container)
2. Missing environment variable or secret?
   → Check: envFrom / env / valueFrom.secretKeyRef in the pod spec
   → Verify all referenced secrets/configmaps exist in the namespace
3. Misconfigured readiness/liveness probe timing?
   → If the app takes >probe.failureThreshold * probe.periodSeconds to start → force crash
4. Application bug / panic on startup?
   → Check previous pod logs for stack traces
5. Resource limits too tight?
   → OOMKilled at startup (check Events: OOMKilled)
6. Wrong entrypoint/command in the image?
""",
        quick_checks=[
            "Run: kubectl logs <pod> --previous",
            "Check all secretKeyRef and configMapKeyRef references exist",
            "Review liveness/readiness probe timing vs app startup time",
            "Check for OOMKilled in pod events",
        ],
    ),

    Pattern(
        name="OOMKilled",
        signals=["OOMKilled", "oom kill", "out of memory", "oom-kill event"],
        severity="high",
        context="""
The container exceeded its memory limit and was killed by the kernel OOM killer.

Diagnostic tree:
1. What is the memory limit set to? (`kubectl describe pod <name>`)
2. What is the actual memory usage at the time of kill?
   → Check metrics: `kubectl top pod <name>` (if metrics-server is installed)
   → Or check Prometheus/Grafana memory usage graphs
3. Is the limit set too low for normal workload?
   → Compare limit vs typical usage over the last 7 days
4. Is there a memory leak?
   → Check if memory grows steadily until OOM (leak) vs spikes (burst)
5. For Java/JVM apps: is -Xmx set? JVM may not respect container limits without it.
6. For Node.js: is --max-old-space-size set?
Resolution: either increase the memory limit or fix the leak.
""",
        quick_checks=[
            "Check current memory limit: kubectl describe pod",
            "Check memory usage trend before the kill",
            "For JVM: verify -Xmx is set below container limit",
            "Look for memory leak pattern (steadily growing vs spike)",
        ],
    ),

    # ── K8s scheduling ────────────────────────────────────────────────────────

    Pattern(
        name="FailedScheduling",
        signals=["FailedScheduling", "Insufficient cpu", "Insufficient memory",
                 "no nodes available", "didn't match node selector",
                 "didn't match pod affinity", "Unschedulable"],
        severity="medium",
        context="""
The Kubernetes scheduler cannot find a node to place this pod.

Diagnostic tree:
1. Resource exhaustion?
   → `kubectl describe nodes` — check Allocatable vs Requests for cpu/memory
   → All nodes may be at capacity. Consider scaling the cluster.
2. Node selector / affinity mismatch?
   → Pod requires a label (e.g. disktype=ssd) that no node has
   → Check: nodeSelector, nodeAffinity in pod spec vs actual node labels
3. Taint/toleration issue?
   → Node has a taint the pod doesn't tolerate
   → `kubectl describe node <name>` shows Taints
4. PodAntiAffinity too strict?
   → Pod can't be placed near other pods due to anti-affinity rules
5. PVC pending?
   → If a PersistentVolumeClaim is unbound, the pod waits forever
""",
        quick_checks=[
            "kubectl describe nodes | grep -A5 'Allocated resources'",
            "Check nodeSelector labels match actual node labels",
            "Check node taints vs pod tolerations",
            "Check PVC status: kubectl get pvc",
        ],
    ),

    # ── K8s secrets/config ────────────────────────────────────────────────────

    Pattern(
        name="MissingSecret",
        signals=["secret", "not found", "couldn't find", "failed to find",
                 "secretKeyRef", "no secret", "secret.*not.*exist"],
        severity="high",
        context="""
A Kubernetes secret referenced by the pod does not exist in the namespace.
This is a very common cause of silent deploy failures — the pipeline succeeds
(it only pushes the image and applies manifests) but the pod never starts
because it can't mount or read a secret.

Diagnostic tree:
1. What secret is missing?
   → `kubectl describe pod <name>` — look for "secret <name> not found"
   → Or check the pod spec: envFrom.secretRef / env.valueFrom.secretKeyRef / volumes.secret
2. Does the secret exist in the correct namespace?
   → `kubectl get secret -n <namespace>` — secrets are namespace-scoped
3. Was the secret supposed to be created by the pipeline?
   → Check the CI/CD pipeline definition — is there a step that creates it?
   → Common mistake: secret is created in production but not staging, or vice versa
4. Is the secret managed by external-secrets / Vault / Sealed Secrets?
   → Check if the ExternalSecret or SealedSecret resource exists and is synced
""",
        quick_checks=[
            "kubectl describe pod <name> | grep -A3 'secret'",
            "kubectl get secret -n <namespace>",
            "Check pipeline for secret creation step",
            "Check ExternalSecret / SealedSecret status if used",
        ],
    ),

    # ── K8s node health ───────────────────────────────────────────────────────

    Pattern(
        name="NodeNotReady",
        signals=["NodeNotReady", "node not ready", "node condition", "kubelet stopped"],
        severity="critical",
        context="""
A Kubernetes node has gone NotReady. Pods on this node may be evicted or stuck Terminating.

Diagnostic tree:
1. Is the kubelet running on the node?
   → SSH to node: `systemctl status kubelet`
2. Is the node reachable at all?
   → `kubectl describe node <name>` — check Conditions and Events
3. Disk pressure? Memory pressure? PID pressure?
   → Check Node Conditions: DiskPressure, MemoryPressure, PIDPressure
4. Network issue?
   → Node may be reachable via SSH but the CNI plugin is broken
5. Certificate expired?
   → kubelet TLS certs expire — check kubelet logs: `journalctl -u kubelet -n 100`
6. Cloud provider issue?
   → Check the cloud console for the underlying VM health
""",
        quick_checks=[
            "kubectl describe node <name>",
            "SSH to node: systemctl status kubelet",
            "journalctl -u kubelet -n 50 --no-pager",
            "Check disk/memory pressure in node conditions",
        ],
    ),

    # ── Linux host problems ───────────────────────────────────────────────────

    Pattern(
        name="DiskFull",
        signals=["no space left on device", "disk full", "filesystem full",
                 "ENOSPC", "wrote 0 bytes", "cannot write"],
        severity="critical",
        context="""
The filesystem is full. This causes cascading failures: logs stop writing,
databases crash, applications fail to create temp files.

Diagnostic tree:
1. Which filesystem? `df -h` — find the one at 100%
2. What is consuming the space?
   → `du -sh /* 2>/dev/null | sort -h` to find large directories
   → Common culprits: old Docker images, container logs, core dumps, old log files
3. For K8s nodes: Docker/containerd image cache?
   → `docker system df` or `crictl images`
   → Old unused images accumulate without a cleanup policy
4. For database nodes: WAL/binlog accumulated?
5. Can you free space immediately?
   → `docker system prune -f` for image cache
   → `journalctl --vacuum-size=500M` for systemd journals
   → Delete old log files
""",
        quick_checks=[
            "df -h",
            "du -sh /* 2>/dev/null | sort -h | tail -20",
            "docker system df (if Docker is running)",
            "journalctl --disk-usage",
        ],
    ),

    Pattern(
        name="HighLoad",
        signals=["load average", "cpu throttl", "high cpu", "system overload",
                 "throttling", "cpu limit"],
        severity="medium",
        context="""
The system or container is CPU-throttled or under high load.

Diagnostic tree:
1. Is it a container CPU limit being hit?
   → `kubectl top pod` — check if CPU is at the limit
   → `kubectl describe pod` — check resources.limits.cpu
2. Is it a host-level load issue?
   → `uptime` — load average > number of CPU cores = overloaded
   → `top` / `htop` — which process is consuming CPU?
3. Is there a runaway process?
   → Check for CPU-spiking processes: `ps aux --sort=-%cpu | head -20`
4. Is this expected load or a regression?
   → Compare to baseline — did a recent deploy change CPU usage pattern?
""",
        quick_checks=[
            "kubectl top pods --sort-by=cpu",
            "kubectl describe pod <name> | grep -A3 'Limits'",
            "uptime && top -bn1 | head -20",
        ],
    ),

    # ── CI/CD pipeline failures ───────────────────────────────────────────────

    Pattern(
        name="PipelineFailed",
        signals=["pipeline failed", "job failed", "exit code 1", "exit code 2",
                 "build failed", "test failed", "deployment failed",
                 "FAILED", "Error: command failed"],
        severity="medium",
        context="""
A CI/CD pipeline job failed. The error may not be visible in the pipeline logs —
the actual failure may be downstream in Kubernetes (see cross-source correlation above).

Diagnostic tree:
1. Is the error in the pipeline logs, or is the pipeline itself succeeding but K8s failing?
   → Check K8s events from the same time window (cross-source correlation is active)
2. Failed tests?
   → Look for test output, assertion errors, or timeout messages
3. Build failure?
   → Compilation error, dependency resolution failure, missing env var in build step
4. Docker build failure?
   → Check Dockerfile syntax, base image availability, build args
5. Push failure?
   → Registry credentials, network, quota
6. Deploy step failure?
   → kubectl apply errors, Helm upgrade errors — these may be in K8s events
""",
        quick_checks=[
            "Check pipeline job logs for the specific failing step",
            "Check K8s events at the same time as the pipeline run",
            "Verify all env vars / secrets referenced in the pipeline exist",
            "Check registry push permissions",
        ],
    ),

    # ── General connectivity ──────────────────────────────────────────────────

    Pattern(
        name="ConnectionRefused",
        signals=["connection refused", "connection reset", "connection timeout",
                 "dial tcp", "ECONNREFUSED", "ETIMEDOUT", "no route to host"],
        severity="medium",
        context="""
A service cannot connect to a dependency (database, cache, another service, external API).

Diagnostic tree:
1. Is the target service running?
   → `kubectl get pods -n <namespace>` — is the dependency pod Running?
2. Is the service DNS resolving correctly?
   → `kubectl exec -it <pod> -- nslookup <service-name>`
3. Is there a network policy blocking the connection?
   → Check NetworkPolicy resources in the namespace
4. Is the port correct?
   → Check the Service spec: `kubectl get svc <name>`
5. Did the dependency just restart or become temporarily unavailable?
   → Check the dependency's own events/logs
6. Is it a transient issue or persistent?
   → A single timeout may be a blip; repeated timeouts indicate a real outage
""",
        quick_checks=[
            "kubectl get pods -n <namespace> (check dependency)",
            "kubectl exec -it <pod> -- nslookup <target-service>",
            "kubectl get networkpolicy -n <namespace>",
            "kubectl get svc <service-name>",
        ],
    ),
]


# ── Match patterns against an event ──────────────────────────────────────────

def match_patterns(message: str, fingerprint: str = "") -> list[Pattern]:
    """Return all patterns that match this event."""
    text = (message + " " + fingerprint).lower()
    return [p for p in PATTERNS if any(s.lower() in text for s in p.signals)]


def build_pattern_context(message: str, fingerprint: str = "") -> str:
    """Build the diagnostic context block to inject into Claude's prompt."""
    matched = match_patterns(message, fingerprint)
    if not matched:
        return ""

    blocks = []
    for p in matched:
        blocks.append(f"### Known pattern: {p.name}\n{p.context.strip()}")
        if p.quick_checks:
            checks = "\n".join(f"- {c}" for c in p.quick_checks)
            blocks.append(f"Quick checks:\n{checks}")

    return "\n\n".join(blocks)


def highest_severity(message: str, fingerprint: str = "") -> str:
    """Return the highest severity among matched patterns."""
    order = ["low", "medium", "high", "critical"]
    matched = match_patterns(message, fingerprint)
    if not matched:
        return "medium"
    return max((p.severity for p in matched), key=lambda s: order.index(s))
