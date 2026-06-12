# Epictetus
## A poor man's load balancer

A production-ready Kubernetes controller that automatically manages Cloudflare DNS records based on node lifecycle events. Epictetus watches nodes and services using **informers** — reacting to changes in real time with zero polling — and fans out DNS operations concurrently across all hostnames.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Epictetus (Go)                      │
│                                                         │
│  SharedInformerFactory                                  │
│  ┌─────────────────┐   ┌──────────────────────────┐    │
│  │  Node Informer  │   │   Service Informer        │    │
│  │  (watch+cache)  │   │   (watch+cache)           │    │
│  └────────┬────────┘   └────────────┬─────────────┘    │
│           │ state change only        │ annotation change │
│           ▼                          ▼                   │
│  ┌─────────────────┐   ┌──────────────────────────┐    │
│  │  Work Queue     │   │   ServiceStore (in-mem)   │    │
│  │  (rate-limited, │   │   (RWMutex map)           │    │
│  │   deduplicated) │   └──────────────────────────┘    │
│  └────────┬────────┘                                    │
│           │ N workers                                   │
│           ▼                                             │
│  ┌─────────────────┐                                    │
│  │   Reconciler    │── goroutines per hostname ──▶ CF   │
│  │   (no K8s API   │                                    │
│  │    calls here)  │                                    │
│  └─────────────────┘                                    │
└─────────────────────────────────────────────────────────┘
```

**How it works:**
1. Node informer delivers ADDED/MODIFIED/DELETED events; a `nodeStateChanged` filter discards high-frequency heartbeat updates and only enqueues on taint, ready condition, or external IP changes
2. Service informer keeps `ServiceStore` up-to-date reactively — no polling, no K8s API calls during reconciliation
3. Work queue deduplicates burst events (e.g. 50 nodes getting a taint simultaneously → 50 queue items processed by N workers in parallel)
4. Reconciler reads service configs from memory, then fans out Cloudflare API calls concurrently across all hostnames
5. A periodic full sync runs as a safety net to catch any missed events

## Features

- **Event-driven**: node state changes trigger immediate DNS reconciliation, not a polling loop
- **Multi-zone**: auto-discovers all Cloudflare zones; no zone IDs needed
- **Service annotation config**: DNS settings live on K8s services (`epictetus.io/dns-enabled`, etc.)
- **Multiple hostnames per service**: comma-separated or JSON array
- **Multi-environment**: cloud provider ExternalIP → Flannel annotation → custom label fallback
- **Conservative autoscaler handling**: requires BOTH deletion taints before removing records
- **Structured logging**: `log/slog` with text (dev) or JSON (prod) format
- **Health endpoints**: `/health`, `/health/live`, `/health/ready`

## Quick Start

### Prerequisites

- Go 1.22+
- Cloudflare API token with DNS edit permissions for all managed zones
- Kubernetes cluster (kubeconfig or in-cluster)

### Build & Run

```bash
# Build
go build -o epictetus .

# Run (local kubeconfig)
export CLOUDFLARE_API_TOKEN="your-token"
export K8S_CONFIG_PATH="$HOME/.kube/config"
./epictetus

# Run in-cluster (no K8S_CONFIG_PATH needed)
export CLOUDFLARE_API_TOKEN="your-token"
./epictetus
```

### Docker

```bash
docker build -t epictetus .
docker run -e CLOUDFLARE_API_TOKEN=your-token epictetus
```

## Configuration

All configuration is via environment variables.

### Required

| Variable | Description |
|----------|-------------|
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with `Zone:DNS:Edit` for all managed zones |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DNS_SYNC_INTERVAL` | `60` | Full sync interval in seconds (minimum 10) |
| `WORKER_COUNT` | `4` | Number of concurrent reconciliation workers |
| `UNHEALTHY_NODE_TAINTS` | `critical` | Taint set that triggers immediate DNS removal (see below) |
| `REMOVE_ON_NOT_READY` | `true` | Remove DNS when node Ready=False |
| `REMOVE_ON_UNREACHABLE` | `true` | Remove DNS when node Ready=Unknown |
| `K8S_CONFIG_PATH` | _(in-cluster)_ | Path to kubeconfig; omit when running inside the cluster |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `LOG_FORMAT` | `console` | `console` (text) or `json` |
| `HEALTH_PORT` | `8080` | Port for the health HTTP server |
| `MAX_RETRIES` | `3` | Cloudflare API retry count |

### Unhealthy Node Taints

| Value | Taints monitored |
|-------|-----------------|
| `none` | None (only deletion taints) |
| `critical` | `not-ready`, `unreachable`, `network-unavailable`, `out-of-service`, `cilium/agent-not-ready` |
| `all` | Critical + `unschedulable` |
| `custom:taint1,taint2` | Comma-separated list |

## Service Configuration

DNS management is configured via Kubernetes service annotations.

### Required Annotations

| Annotation | Description |
|------------|-------------|
| `epictetus.io/dns-enabled: "true"` | Enable DNS management |
| `epictetus.io/hostname: "api.example.com"` | Single hostname (legacy) |
| `epictetus.io/hostnames: "a.example.com,b.example.com"` | Multiple hostnames |

### Optional Annotations

| Annotation | Default | Description |
|------------|---------|-------------|
| `epictetus.io/ttl` | `300` | DNS record TTL in seconds |
| `epictetus.io/proxied` | `false` | Enable Cloudflare proxy |
| `epictetus.io/control-plane-only` | `false` | When `true`, only announce control-plane node IPs; worker node IPs are ignored and removed if present |

### Examples

```yaml
# Single hostname
apiVersion: v1
kind: Service
metadata:
  name: frontend
  annotations:
    epictetus.io/dns-enabled: "true"
    epictetus.io/hostname: "app.example.com"
    epictetus.io/ttl: "60"
    epictetus.io/proxied: "true"
spec:
  selector:
    app: frontend
  ports:
    - port: 80
```

```yaml
# Multiple hostnames across different Cloudflare zones
apiVersion: v1
kind: Service
metadata:
  name: api
  annotations:
    epictetus.io/dns-enabled: "true"
    epictetus.io/hostnames: '["api.example.com", "api.company.org", "api.startup.io"]'
    epictetus.io/ttl: "300"
spec:
  selector:
    app: api
  ports:
    - port: 443
```

```yaml
# Control-plane nodes only — useful for internal/admin services that should
# not be reachable via worker IPs
apiVersion: v1
kind: Service
metadata:
  name: admin
  annotations:
    epictetus.io/dns-enabled: "true"
    epictetus.io/hostname: "admin.example.com"
    epictetus.io/control-plane-only: "true"
    epictetus.io/ttl: "300"
spec:
  selector:
    app: admin
  ports:
    - port: 443
```

## DNS Record Removal Triggers

### Immediate removal
- Node `Ready=False` (configurable via `REMOVE_ON_NOT_READY`)
- Node `Ready=Unknown` (configurable via `REMOVE_ON_UNREACHABLE`)
- Any taint in the `UNHEALTHY_NODE_TAINTS` set

### Conservative removal (cluster autoscaler)
- **Both** `DeletionCandidateOfClusterAutoscaler` AND `ToBeDeletedByClusterAutoscaler` present

### Node deleted
- Immediate removal using the cached IP

## External IP Detection

Three-stage fallback, tried in order:

1. `node.status.addresses[type=ExternalIP]` — standard cloud providers (EKS, GKE, AKS)
2. `flannel.alpha.coreos.com/public-ip` annotation — bare metal with Flannel CNI
3. `k8s.magicorn.net/external-ip` label — manual override

## Kubernetes Deployment

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/deployment.yaml
```

Required RBAC: `get/list/watch` on `nodes` and `services`.

## Health Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health/live` | Liveness probe — always 200 if the process is running |
| `GET /health/ready` | Readiness probe — 503 until the first full sync completes |
| `GET /health` | Full status including Cloudflare connectivity and available zones |

## Why "Epictetus"?

Epictetus was a Stoic philosopher who taught focusing on what you can control and accepting what you cannot. This service maintains equilibrium by responding to external events (node lifecycle) rather than fighting them — conservative on deletion, immediate on failure.

*"You have power over your mind — not outside events. Realize this, and you will find strength."* — Epictetus

## License

GNU General Public License v3.0
