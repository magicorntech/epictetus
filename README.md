# Epictetus
## A poor man's load balancer

A production-ready standalone service that automatically manages CloudFlare DNS records based on Kubernetes node lifecycle events. Epictetus monitors for cluster autoscaler taints and automatically removes DNS records only when **BOTH** deletion taints are present on a node, ensuring conservative and reliable DNS management.

## Features

- **Real-time Node Monitoring**: Watches Kubernetes nodes for deletion taints (`DeletionCandidateOfClusterAutoscaler`, `ToBeDeletedByClusterAutoscaler`)
- **Conservative DNS Management**: Only removes DNS records when **BOTH** deletion taints are present
- **Automatic DNS Management**: Creates/deletes CloudFlare DNS A records based on node external IPs
- **Multi-hostname Support**: Manages multiple DNS hostnames (e.g., `ap1-test.example.com`, `ap2-test.example.com`)
- **Scheduled Synchronization**: Performs full DNS synchronization every minute to ensure consistency
- **Live Event Processing**: Responds immediately to Kubernetes node events
- **Production Ready**: Includes health checks, structured logging, and error handling
- **Battle Tested**: Designed for high availability with retry logic and graceful degradation

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Kubernetes    │    │    Epictetus    │    │   CloudFlare    │
│     Cluster     │───▶│  DNS Manager    │───▶│      API        │
│                 │    │                 │    │                 │
│ • Node Events   │    │ • Event Watch   │    │ • DNS Records   │
│ • Taints        │    │ • Sync Jobs     │    │ • A Records     │
│ • External IPs  │    │ • Health Check  │    │ • Zone Mgmt     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- CloudFlare API Token with DNS edit permissions
- Kubernetes cluster access (via kubeconfig or in-cluster)
- CloudFlare Zone ID for your domain

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
# Copy example environment file
cp .env.example .env

# Edit with your values
export CLOUDFLARE_API_TOKEN="your-api-token"
export CLOUDFLARE_ZONE_ID="your-zone-id"
export DNS_HOSTNAMES="ap1-test.example.com,ap2-test.example.com"
```

4. **Run Epictetus**:
```bash
python main.py
```

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `CLOUDFLARE_API_TOKEN` | CloudFlare API token with DNS edit permissions | `abc123...` |
| `CLOUDFLARE_ZONE_ID` | CloudFlare Zone ID for your domain | `def456...` |
| `DNS_HOSTNAMES` | Comma-separated list of hostnames to manage | `ap1-test.example.com,ap2-test.example.com` |

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

## Health Check Endpoints

When `ENABLE_HEALTH_SERVER=true`, Epictetus provides health check endpoints:

- `GET /health` - Basic health check
- `GET /health/ready` - Kubernetes readiness probe
- `GET /health/live` - Kubernetes liveness probe

### Example Health Checks

```bash
# Check service health
curl http://localhost:8080/health

# Check if ready to serve traffic
curl http://localhost:8080/health/ready

# Simple liveness check
curl http://localhost:8080/health/live
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
CLOUDFLARE_API_TOKEN=your-api-token
CLOUDFLARE_ZONE_ID=your-zone-id
DNS_HOSTNAMES=ap1-test.example.com,ap2-test.example.com
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

# Update k8s/namespace.yaml with the encoded values
kubectl apply -f k8s/namespace.yaml
```

3. **Deploy Epictetus**:
```bash
kubectl apply -f k8s/deployment.yaml
```

4. **Check deployment status**:
```bash
kubectl get pods -n epictetus
kubectl logs -f deployment/epictetus -n epictetus
```

### Kubernetes Permissions

Epictetus requires the following Kubernetes RBAC permissions:
- **Nodes**: `get`, `list`, `watch` (to monitor node taints)
- **Services**: `get`, `list`, `watch` (to scan service annotations)

## How It Works

### The "Poor Man's Load Balancer" Concept

Traditional load balancers are expensive and complex. Epictetus provides a simpler approach:

1. **DNS-based Load Balancing**: Instead of a hardware/software load balancer, uses multiple A records for the same hostname
2. **Automatic Node Management**: As nodes join/leave the cluster, DNS records are automatically updated
3. **Cost Effective**: Uses existing DNS infrastructure instead of dedicated load balancer resources
4. **Cloud Native**: Integrates naturally with Kubernetes cluster autoscaling

### Node Lifecycle Management

**Conservative Approach - Requires BOTH Taints:**

Epictetus only removes DNS records when a node has **BOTH** deletion taints present:

1. **Node Added**: When a new node joins the cluster, DNS A records are created for all configured hostnames pointing to the node's external IP

2. **First Taint Applied** (`DeletionCandidateOfClusterAutoscaler`):
   - Node is identified as a candidate for deletion
   - **Epictetus takes NO action** - waits for confirmation

3. **Second Taint Applied** (`ToBeDeletedByClusterAutoscaler`):
   - Node is confirmed for deletion
   - **Epictetus removes DNS records immediately** ⚡

4. **Node Deleted**: When a node is removed from the cluster, any remaining DNS records are cleaned up

This conservative approach ensures that DNS records are only removed when the cluster autoscaler has definitively decided to delete the node, preventing premature removal during temporary scaling decisions.

### Synchronization Process

- **Live Events**: Real-time processing of Kubernetes node events
- **Scheduled Sync**: Full synchronization every minute to catch any missed events
- **Consistency Check**: Ensures DNS records match the current cluster state

### DNS Record Management

- **Multi-A Records**: Supports multiple A records per hostname (round-robin DNS)
- **IP-based Cleanup**: Removes only records matching specific node IPs
- **Atomic Operations**: Uses CloudFlare API for reliable record management

## Monitoring

### Health Checks

Epictetus provides multiple health check endpoints for monitoring:

- **Liveness**: `/health/live` - Simple alive check
- **Readiness**: `/health/ready` - Checks if service is ready to serve traffic
- **Basic**: `/health` - General health information

### Logging

Epictetus uses structured logging with configurable formats:

- **Console**: Human-readable format for development
- **JSON**: Structured format for production log aggregation

## Troubleshooting

### Common Issues

1. **CloudFlare API Authentication**:
   ```bash
   # Test your API token
   curl -X GET "https://api.cloudflare.com/client/v4/user/tokens/verify" \
        -H "Authorization: Bearer YOUR_API_TOKEN"
   ```

2. **Kubernetes Permissions**:
   ```bash
   # Check service account permissions
   kubectl auth can-i get nodes --as=system:serviceaccount:epictetus:epictetus
   ```

3. **DNS Records Not Updating**:
   - Check the logs for API errors
   - Verify node external IPs are accessible
   - Ensure hostnames are correctly configured
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
- **Permissions**: `Zone:DNS:Edit` for your specific zone
- **Zone Resources**: Include only the zones you need to manage
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
