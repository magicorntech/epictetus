# Epictetus
## A poor man's load balancer

A production-ready standalone service that automatically manages CloudFlare DNS records across **multiple zones** based on Kubernetes node lifecycle events. Epictetus monitors for cluster autoscaler taints and automatically removes DNS records only when **BOTH** deletion taints are present on a node, ensuring conservative and reliable DNS management.

## Features

- **🌐 Multi-Zone Support**: Automatically manages DNS across multiple CloudFlare zones/domains
- **🔍 Auto Zone Detection**: No need to specify zone IDs - automatically detects which zone each hostname belongs to
- **📋 Service Annotation Configuration**: DNS settings configured via Kubernetes service annotations (no ConfigMaps needed)
- **Real-time Node Monitoring**: Watches Kubernetes nodes for deletion taints (`DeletionCandidateOfClusterAutoscaler`, `ToBeDeletedByClusterAutoscaler`)
- **Conservative DNS Management**: Only removes DNS records when **BOTH** deletion taints are present
- **Automatic DNS Management**: Creates/deletes CloudFlare DNS A records based on node external IPs
- **Service-Specific Settings**: Each service can have different TTL, proxy settings, and hostnames
- **Scheduled Synchronization**: Performs full DNS synchronization every minute to ensure consistency
- **Live Event Processing**: Responds immediately to Kubernetes node events
- **Production Ready**: Includes health checks, structured logging, and error handling
- **Battle Tested**: Designed for high availability with retry logic and graceful degradation

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Kubernetes    │    │    Epictetus    │    │   CloudFlare    │
│     Cluster     │───▶│  DNS Manager    │───▶│   Multi-Zone    │
│                 │    │                 │    │      API        │
│ • Node Events   │    │ • Event Watch   │    │ • DNS Records   │
│ • Taints        │    │ • Zone Detect   │    │ • A Records     │
│ • Service       │    │ • Sync Jobs     │    │ • Multi-Domain  │
│   Annotations   │    │ • Health Check  │    │ • Zone Mgmt     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- CloudFlare API Token with DNS edit permissions for **all zones** you want to manage
- Kubernetes cluster access (via kubeconfig or in-cluster)
- Services annotated with Epictetus DNS management settings

### Environment Setup

1. **Clone the repository**:
```bash
git clone <repository-url>
cd epictetus
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Configure environment variables**:
```bash
# Only ONE environment variable is required now!
export CLOUDFLARE_API_TOKEN="your-api-token"

# That's it! No zone IDs needed - automatic detection
```

4. **Annotate your services** (see Service Configuration section below)

5. **Run Epictetus**:
```bash
python main.py
```

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CLOUDFLARE_API_TOKEN` | CloudFlare API token with DNS edit permissions for **all zones** | `abc123...` |

**Note**: `CLOUDFLARE_ZONE_ID` is **no longer required** - zones are automatically detected!

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DNS_SYNC_INTERVAL` | `60` | Full synchronization interval (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FORMAT` | `console` | Log format (`console` or `json`) |
| `HEALTH_CHECK_INTERVAL` | `30` | Health check interval (seconds) |
| `ENABLE_HEALTH_SERVER` | `true` | Enable health check HTTP server |
| `HEALTH_PORT` | `8080` | Port for health check server |
| `K8S_CONFIG_PATH` | `None` | Path to kubeconfig file (leave empty for in-cluster) |
| `MAX_RETRIES` | `3` | Maximum retries for API calls |
| `RETRY_DELAY` | `5` | Delay between retries (seconds) |

## Service Configuration

DNS management is configured entirely through **Kubernetes service annotations**. No ConfigMaps or hardcoded configuration needed!

### Required Annotations

| Annotation | Description | Example |
|------------|-------------|---------|
| `epictetus.io/dns-enabled` | Enable DNS management for this service | `"true"` |
| `epictetus.io/hostname` | The DNS hostname to manage | `"api.example.com"` |

### Optional Annotations

| Annotation | Default | Description | Example |
|------------|---------|-------------|---------|
| `epictetus.io/ttl` | `300` | TTL in seconds for DNS records | `"600"` |
| `epictetus.io/proxied` | `false` | Enable CloudFlare proxy | `"true"` |

### Service Configuration Examples

```yaml
# Frontend service with short TTL and proxy enabled
apiVersion: v1
kind: Service
metadata:
  name: frontend
  namespace: production
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
      targetPort: 8080

---
# API service with longer TTL and no proxy (different domain/zone)
apiVersion: v1
kind: Service
metadata:
  name: api
  namespace: production
  annotations:
    epictetus.io/dns-enabled: "true"
    epictetus.io/hostname: "api.different-domain.com"
    epictetus.io/ttl: "300"
    epictetus.io/proxied: "false"
spec:
  selector:
    app: api
  ports:
    - port: 443
      targetPort: 8080

---
# Backend service (third domain/zone)
apiVersion: v1
kind: Service
metadata:
  name: backend
  namespace: production
  annotations:
    epictetus.io/dns-enabled: "true"
    epictetus.io/hostname: "backend.another-domain.org"
    epictetus.io/ttl: "600"
    epictetus.io/proxied: "true"
spec:
  selector:
    app: backend
  ports:
    - port: 80
      targetPort: 3000
```

## Multi-Zone Support

### Automatic Zone Detection

Epictetus automatically:
- 🔍 **Discovers all CloudFlare zones** in your account at startup
- 🎯 **Maps each hostname** to the correct zone (e.g., `api.example.com` → `example.com` zone)
- 💾 **Caches zone mappings** for performance
- 🌐 **Manages multiple domains** simultaneously across different zones

### Example Multi-Domain Setup

```yaml
# These services can all be managed simultaneously:
# • app.example.com        (Zone: example.com)
# • api.company.org        (Zone: company.org)  
# • backend.startup.io     (Zone: startup.io)
# • frontend.mybrand.net   (Zone: mybrand.net)
```

### Benefits

- **No Zone Configuration**: No need to specify zone IDs
- **Dynamic**: Add new domains without redeploying Epictetus
- **Scalable**: Supports unlimited zones and domains
- **Error Handling**: Clear errors if hostname doesn't match any zone

## Health Check Endpoints

When `ENABLE_HEALTH_SERVER=true`, Epictetus provides health check endpoints:

- `GET /health` - Basic health check with zone information
- `GET /health/ready` - Kubernetes readiness probe
- `GET /health/live` - Kubernetes liveness probe

### Example Health Check Response

```bash
curl http://localhost:8080/health
```

```json
{
  "status": "healthy",
  "kubernetes_status": {"status": "healthy"},
  "cloudflare_status": {
    "status": "healthy",
    "available_zones": 3,
    "zones": ["example.com", "company.org", "startup.io"]
  },
  "dns_sync_status": {
    "last_sync": "2024-01-15T10:30:00Z",
    "active_service_configs": 5
  }
}
```

## Docker Deployment

### Build and Run

```bash
# Build the image
docker build -t epictetus .

# Run with docker-compose
docker-compose up -d
```

### Environment Variables for Docker

Create a `.env` file in the project root:

```bash
# Only one variable needed!
CLOUDFLARE_API_TOKEN=your-api-token
```

## Kubernetes Deployment

### Deploy to Kubernetes

1. **Create namespace and secrets**:
```bash
kubectl apply -f k8s/namespace.yaml
```

2. **Update secrets with your values**:
```bash
# Encode your CloudFlare API token
echo -n "your-api-token" | base64

# Update the secret in k8s/namespace.yaml with the encoded value
kubectl apply -f k8s/namespace.yaml
```

3. **Deploy Epictetus**:
```bash
kubectl apply -f k8s/deployment.yaml
```

4. **Annotate your services** (see Service Configuration examples above)

5. **Check deployment status**:
```bash
kubectl get pods -n epictetus
kubectl logs -f deployment/epictetus -n epictetus
```

### Kubernetes Permissions

Epictetus requires the following Kubernetes RBAC permissions:
- **Nodes**: `get`, `list`, `watch` (to monitor node taints)
- **Services**: `get`, `list`, `watch` (to read service annotations)

## How It Works

### The "Poor Man's Load Balancer" Concept

Traditional load balancers are expensive and complex. Epictetus provides a simpler approach:

1. **DNS-based Load Balancing**: Instead of a hardware/software load balancer, uses multiple A records for the same hostname
2. **Automatic Node Management**: As nodes join/leave the cluster, DNS records are automatically updated
3. **Cost Effective**: Uses existing DNS infrastructure instead of dedicated load balancer resources
4. **Cloud Native**: Integrates naturally with Kubernetes cluster autoscaling
5. **Multi-Domain**: Supports multiple domains across different CloudFlare zones

### Node Lifecycle Management

**Conservative Approach - Requires BOTH Taints:**

Epictetus manages DNS records throughout the complete node lifecycle:

1. **Node Added**: 
   - ✅ **DNS A records are immediately created** for all configured hostnames pointing to the node's external IP
   - ✅ Works in real-time via Kubernetes event watching
   - ✅ Uses service-specific settings (TTL, proxy, etc.)
   - ✅ Skips creation only if node already has both deletion taints

2. **First Taint Applied** (`DeletionCandidateOfClusterAutoscaler`):
   - Node is identified as a candidate for deletion
   - **Epictetus takes NO action** - waits for confirmation
   - DNS records remain active

3. **Second Taint Applied** (`ToBeDeletedByClusterAutoscaler`):
   - Node is confirmed for deletion
   - **Epictetus removes DNS records immediately** ⚡
   - Conservative approach ensures only confirmed deletions trigger DNS removal

4. **Node Deleted**: When a node is removed from the cluster, any remaining DNS records are cleaned up

5. **Background Synchronization**:
   - ✅ **Creates missing DNS records** for healthy nodes every minute
   - ✅ **Removes DNS records** for IPs that no longer exist
   - ✅ Works across all zones and domains
   - ✅ Ensures consistency even if real-time events are missed

This conservative approach ensures that DNS records are only removed when the cluster autoscaler has definitively decided to delete the node, preventing premature removal during temporary scaling decisions.

### Synchronization Process

- **Live Events**: Real-time processing of Kubernetes node events
- **Scheduled Sync**: Full synchronization every minute to catch any missed events
- **Multi-Zone Consistency**: Ensures DNS records match the current cluster state across all zones
- **Service Discovery**: Automatically refreshes service configurations from annotations

### DNS Record Management

- **Multi-A Records**: Supports multiple A records per hostname (round-robin DNS)
- **IP-based Cleanup**: Removes only records matching specific node IPs
- **Zone-Aware Operations**: Routes DNS operations to the correct CloudFlare zone
- **Service-Specific Settings**: Each service can have different TTL and proxy settings

## Monitoring

### Health Checks

Epictetus provides multiple health check endpoints for monitoring:

- **Liveness**: `/health/live` - Simple alive check
- **Readiness**: `/health/ready` - Checks if service is ready to serve traffic  
- **Comprehensive**: `/health` - Detailed health information including zone status

### Logging

Epictetus uses structured logging with configurable formats:

- **Console**: Human-readable format for development
- **JSON**: Structured format for production log aggregation

### Metrics

- **Zone Discovery**: Tracks available CloudFlare zones
- **Service Configurations**: Monitors active service annotations
- **DNS Operations**: Logs creation, deletion, and sync operations
- **Error Tracking**: Comprehensive error logging with context

## Troubleshooting

### Common Issues

1. **CloudFlare API Authentication**:
   ```bash
   # Test your API token
   curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
        -H "Authorization: Bearer YOUR_API_TOKEN"
   ```

2. **Zone Detection Issues**:
   - Check that your hostname's domain exists in CloudFlare
   - Verify your API token has access to the zone
   - Look for zone warnings in the logs

3. **Service Annotation Format**:
   ```bash
   # Check service annotations
   kubectl get service <service-name> -o yaml
   
   # Should include:
   # epictetus.io/dns-enabled: "true"
   # epictetus.io/hostname: "your-hostname.com"
   ```

4. **Kubernetes Permissions**:
   ```bash
   # Check service account permissions
   kubectl auth can-i get nodes --as=system:serviceaccount:epictetus:epictetus
   kubectl auth can-i get services --as=system:serviceaccount:epictetus:epictetus
   ```

5. **DNS Records Not Updating**:
   - Check the logs for API errors
   - Verify node external IPs are accessible
   - Ensure hostnames match domains in your CloudFlare zones
   - Confirm that **BOTH** deletion taints are present before expecting DNS removal

### Debugging

Enable debug logging:
```bash
export LOG_LEVEL=DEBUG
python main.py
```

Check service status:
```bash
curl http://localhost:8080/health
```

View service configurations:
```bash
# Check which services Epictetus is managing
kubectl get services -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"/"}{.metadata.name}{"\t"}{.metadata.annotations.epictetus\.io/dns-enabled}{"\t"}{.metadata.annotations.epictetus\.io/hostname}{"\n"}{end}' | grep true
```

## Why "Epictetus"?

Epictetus was a Greek Stoic philosopher who taught that we should focus on what we can control and accept what we cannot. This philosophy aligns perfectly with this project:

- **Focus on what we can control**: DNS records, node monitoring, synchronization
- **Accept what we cannot**: Kubernetes cluster decisions, node lifecycle events
- **Respond appropriately**: React to changes without fighting the system

Just like the philosopher, this service maintains equilibrium by responding wisely to external events rather than trying to control them. The conservative approach of requiring both taints reflects this wisdom - patience and careful observation before action.

## Security

### Best Practices

- **Least Privilege**: Service account has minimal required permissions
- **Secret Management**: Sensitive data stored in Kubernetes secrets
- **Non-root User**: Container runs as non-root user
- **Network Policies**: Recommended to implement network policies

### CloudFlare API Token

Create a custom API token with:
- **Permissions**: `Zone:DNS:Edit` for all zones you want to manage
- **Zone Resources**: Include all zones you need to manage
- **IP Restrictions**: Optionally restrict to your cluster IPs

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

## Support

For issues and questions:
1. Check the troubleshooting section
2. Check application logs
3. Open an issue on GitHub

---

**"You have power over your mind - not outside events. Realize this, and you will find strength."** - Epictetus

*Epictetus: A poor man's load balancer that finds strength in simplicity and wisdom in patience.*
