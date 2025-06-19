"""Health check endpoints for monitoring and liveness probes."""

from flask import Blueprint, current_app, jsonify
from datetime import datetime

from app.logger import get_logger


logger = get_logger(__name__)
bp = Blueprint('health', __name__)


@bp.route('/health', methods=['GET'])
def health_check():
    """Basic health check endpoint for load balancers."""
    try:
        # Basic health check - just return OK if the app is running
        return jsonify({
            'status': 'healthy',
            'timestamp': datetime.now().isoformat(),
            'service': 'epictetus',
            'description': 'A poor man\'s load balancer'
        })
    except Exception as e:
        logger.error("Basic health check failed", error=str(e))
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@bp.route('/health/ready', methods=['GET'])
def readiness_check():
    """Readiness check for Kubernetes readiness probe."""
    try:
        # Check if DNS manager is available and ready
        dns_manager = getattr(current_app, 'dns_manager', None)
        if not dns_manager:
            return jsonify({
                'status': 'not_ready',
                'reason': 'DNS manager not initialized',
                'timestamp': datetime.now().isoformat(),
                'service': 'epictetus'
            }), 503
        
        # Check if clients are healthy
        health_status = dns_manager.get_health_status()
        
        if health_status.status in ['healthy', 'degraded']:
            return jsonify({
                'status': 'ready',
                'health_status': health_status.status,
                'timestamp': datetime.now().isoformat(),
                'service': 'epictetus'
            })
        else:
            return jsonify({
                'status': 'not_ready',
                'health_status': health_status.status,
                'errors': health_status.errors,
                'timestamp': datetime.now().isoformat(),
                'service': 'epictetus'
            }), 503
            
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        return jsonify({
            'status': 'not_ready',
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'service': 'epictetus'
        }), 500


@bp.route('/health/live', methods=['GET'])
def liveness_check():
    """Liveness check for Kubernetes liveness probe."""
    try:
        # Simple liveness check - if we can respond, we're alive
        # This should be lightweight and not depend on external services
        return jsonify({
            'status': 'alive',
            'timestamp': datetime.now().isoformat(),
            'service': 'epictetus'
        })
    except Exception as e:
        logger.error("Liveness check failed", error=str(e))
        return jsonify({
            'status': 'dead',
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'service': 'epictetus'
        }), 500


@bp.route('/health/detailed', methods=['GET'])
def detailed_health_check():
    """Detailed health check with full system status."""
    try:
        dns_manager = getattr(current_app, 'dns_manager', None)
        if not dns_manager:
            return jsonify({
                'status': 'unhealthy',
                'reason': 'DNS manager not initialized',
                'timestamp': datetime.now().isoformat()
            }), 500
        
        # Get detailed health status
        health_status = dns_manager.get_health_status()
        
        # Get additional metrics
        recent_events = dns_manager.get_recent_events(10)
        recent_reports = dns_manager.get_recent_sync_reports(5)
        
        # Build detailed response
        response = {
            'status': health_status.status,
            'timestamp': health_status.timestamp.isoformat(),
            'components': {
                'kubernetes': health_status.kubernetes_status,
                'cloudflare': health_status.cloudflare_status,
                'dns_sync': health_status.dns_sync_status
            },
            'last_sync': health_status.last_sync.isoformat() if health_status.last_sync else None,
            'errors': health_status.errors,
            'metrics': {
                'recent_events_count': len(recent_events),
                'recent_sync_reports_count': len(recent_reports),
                'successful_events': sum(1 for e in recent_events if e.success),
                'failed_events': sum(1 for e in recent_events if not e.success)
            }
        }
        
        # Set HTTP status based on health
        status_code = 200
        if health_status.status == 'unhealthy':
            status_code = 503
        elif health_status.status == 'degraded':
            status_code = 200  # Still return 200 for degraded
            
        return jsonify(response), status_code
        
    except Exception as e:
        logger.error("Detailed health check failed", error=str(e))
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@bp.route('/metrics', methods=['GET'])
def metrics():
    """Prometheus-style metrics endpoint."""
    try:
        dns_manager = getattr(current_app, 'dns_manager', None)
        if not dns_manager:
            return "# DNS manager not initialized\n", 503
        
        # Get basic metrics
        health_status = dns_manager.get_health_status()
        recent_events = dns_manager.get_recent_events(100)
        recent_reports = dns_manager.get_recent_sync_reports(10)
        
        # Calculate metrics
        total_events = len(recent_events)
        successful_events = sum(1 for e in recent_events if e.success)
        failed_events = total_events - successful_events
        
        last_sync_timestamp = 0
        if health_status.last_sync:
            last_sync_timestamp = health_status.last_sync.timestamp()
        
        # Get current DNS state
        try:
            current_state = dns_manager.get_current_dns_state()
            total_nodes = current_state.get('cluster_state', {}).get('total_nodes', 0)
            healthy_nodes = current_state.get('cluster_state', {}).get('healthy_nodes', 0)
            deletion_taint_nodes = current_state.get('cluster_state', {}).get('nodes_with_deletion_taints', 0)
            total_dns_records = current_state.get('dns_state', {}).get('total_records', 0)
        except:
            total_nodes = healthy_nodes = deletion_taint_nodes = total_dns_records = 0
        
        # Build Prometheus format metrics
        metrics_text = f"""# HELP dns_manager_health_status Health status of the DNS manager (1=healthy, 0.5=degraded, 0=unhealthy)
# TYPE dns_manager_health_status gauge
dns_manager_health_status {{'status': '{health_status.status}'}} {'1' if health_status.status == 'healthy' else '0.5' if health_status.status == 'degraded' else '0'}

# HELP dns_manager_events_total Total number of DNS management events
# TYPE dns_manager_events_total counter
dns_manager_events_total {total_events}

# HELP dns_manager_events_successful_total Total number of successful DNS management events
# TYPE dns_manager_events_successful_total counter
dns_manager_events_successful_total {successful_events}

# HELP dns_manager_events_failed_total Total number of failed DNS management events
# TYPE dns_manager_events_failed_total counter
dns_manager_events_failed_total {failed_events}

# HELP dns_manager_last_sync_timestamp Unix timestamp of last successful sync
# TYPE dns_manager_last_sync_timestamp gauge
dns_manager_last_sync_timestamp {last_sync_timestamp}

# HELP dns_manager_sync_reports_total Total number of sync reports
# TYPE dns_manager_sync_reports_total counter
dns_manager_sync_reports_total {len(recent_reports)}

# HELP dns_manager_cluster_nodes_total Total number of nodes in the cluster
# TYPE dns_manager_cluster_nodes_total gauge
dns_manager_cluster_nodes_total {total_nodes}

# HELP dns_manager_cluster_nodes_healthy Total number of healthy nodes in the cluster
# TYPE dns_manager_cluster_nodes_healthy gauge
dns_manager_cluster_nodes_healthy {healthy_nodes}

# HELP dns_manager_cluster_nodes_deletion_taints Total number of nodes with deletion taints
# TYPE dns_manager_cluster_nodes_deletion_taints gauge
dns_manager_cluster_nodes_deletion_taints {deletion_taint_nodes}

# HELP dns_manager_dns_records_total Total number of DNS records managed
# TYPE dns_manager_dns_records_total gauge
dns_manager_dns_records_total {total_dns_records}

# HELP dns_manager_kubernetes_api_status Kubernetes API connectivity status (1=connected, 0=disconnected)
# TYPE dns_manager_kubernetes_api_status gauge
dns_manager_kubernetes_api_status {{'1' if health_status.kubernetes_status.get('api_accessible', False) else '0'}}

# HELP dns_manager_cloudflare_api_status CloudFlare API connectivity status (1=connected, 0=disconnected)
# TYPE dns_manager_cloudflare_api_status gauge
dns_manager_cloudflare_api_status {{'1' if health_status.cloudflare_status.get('api_accessible', False) else '0'}}
"""
        
        return metrics_text, 200, {'Content-Type': 'text/plain; charset=utf-8'}
        
    except Exception as e:
        logger.error("Metrics endpoint failed", error=str(e))
        return f"# Error generating metrics: {str(e)}\n", 500, {'Content-Type': 'text/plain; charset=utf-8'} 