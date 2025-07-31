"""Kubernetes client for monitoring nodes and taints."""

import asyncio
from typing import Dict, List, Optional, Set, Tuple, Callable
from kubernetes import client, config, watch
from kubernetes.client.rest import ApiException
from retrying import retry
import threading
import time

from app.logger import get_logger
from app.models import NodeInfo, TaintInfo, ServiceDNSConfig


logger = get_logger(__name__)


class KubernetesClient:
    """Production-ready Kubernetes client with event watching and monitoring."""
    
    def __init__(self, app_config):
        """Initialize Kubernetes client."""
        self.config = app_config
        self.v1 = None
        self.watch_thread = None
        self.watch_active = False
        self.node_cache: Dict[str, NodeInfo] = {}
        self.event_callbacks: List[Callable] = []
        
        # Initialize kubernetes client
        self._init_client()
        
        logger.info("Kubernetes client initialized", 
                   config_path=self.config.get('K8S_CONFIG_PATH'))
    
    def _init_client(self):
        """Initialize Kubernetes client configuration."""
        try:
            if self.config.get('K8S_CONFIG_PATH'):
                # Load from config file (development)
                config.load_kube_config(config_file=self.config['K8S_CONFIG_PATH'])
                logger.info("Loaded Kubernetes config from file")
            else:
                # Load in-cluster config (production)
                config.load_incluster_config()
                logger.info("Loaded Kubernetes in-cluster config")
            
            self.v1 = client.CoreV1Api()
            
        except Exception as e:
            logger.error("Failed to initialize Kubernetes client", error=str(e))
            raise
    
    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def get_all_nodes(self) -> List[NodeInfo]:
        """Get all nodes with their taints and external IPs."""
        try:
            nodes = self.v1.list_node()
            node_list = []
            
            for node in nodes.items:
                node_info = self._extract_node_info(node)
                node_list.append(node_info)
                
                # Update cache
                self.node_cache[node_info.name] = node_info
            
            logger.info("Retrieved nodes from cluster", count=len(node_list))
            return node_list
            
        except ApiException as e:
            logger.error("Kubernetes API error getting nodes", 
                        status=e.status, reason=e.reason)
            raise
        except Exception as e:
            logger.error("Unexpected error getting nodes", error=str(e))
            raise
    
    def _extract_node_info(self, node) -> NodeInfo:
        """Extract relevant information from a Kubernetes node object.
        
        For external IP detection:
        1. First checks node.status.addresses for ExternalIP type
        2. Falls back to flannel.alpha.coreos.com/public-ip annotation if no ExternalIP found
        """
        # Get node name
        name = node.metadata.name
        
        # Extract external IP
        external_ip = None
        if node.status.addresses:
            for addr in node.status.addresses:
                if addr.type == "ExternalIP":
                    external_ip = addr.address
                    break
        
        # Fallback: Check Flannel annotation if no external IP found
        if not external_ip:
            annotations = node.metadata.annotations or {}
            flannel_public_ip = annotations.get('flannel.alpha.coreos.com/public-ip')
            if flannel_public_ip:
                external_ip = flannel_public_ip
                logger.debug("Using Flannel public IP annotation", 
                           node_name=node.metadata.name, 
                           external_ip=external_ip)
        
        # Extract taints
        taints = []
        if node.spec.taints:
            for taint in node.spec.taints:
                taint_info = TaintInfo(
                    key=taint.key,
                    value=taint.value or "",
                    effect=taint.effect
                )
                taints.append(taint_info)
        
        # Check if node has ALL required deletion taints
        deletion_taints = []
        required_deletion_taints = set(self.config.get('DELETION_TAINTS', []))
        present_deletion_taints = set()
        
        for taint in taints:
            if taint.key in required_deletion_taints:
                deletion_taints.append(taint)
                present_deletion_taints.add(taint.key)
        
        # Only consider node as having deletion taints if ALL required taints are present
        if len(present_deletion_taints) < len(required_deletion_taints):
            deletion_taints = []  # Clear if not all taints are present
        
        # Extract labels and annotations
        labels = node.metadata.labels or {}
        annotations = node.metadata.annotations or {}
        
        # Determine node status
        ready = False
        if node.status.conditions:
            for condition in node.status.conditions:
                if condition.type == "Ready":
                    ready = condition.status == "True"
                    break
        
        return NodeInfo(
            name=name,
            external_ip=external_ip,
            taints=taints,
            deletion_taints=deletion_taints,
            labels=labels,
            annotations=annotations,
            ready=ready,
            creation_timestamp=node.metadata.creation_timestamp
        )
    
    def get_nodes_with_deletion_taints(self) -> List[NodeInfo]:
        """Get nodes that have ALL deletion taints (both DeletionCandidate and ToBeDeleted)."""
        nodes = self.get_all_nodes()
        return [node for node in nodes if node.deletion_taints]
    
    def register_event_callback(self, callback: Callable[[str, NodeInfo], None]):
        """Register a callback for node events."""
        self.event_callbacks.append(callback)
        logger.info("Registered event callback", callback=callback.__name__)
    
    def start_watching(self):
        """Start watching for node events in a separate thread."""
        if self.watch_active:
            logger.warning("Watch already active")
            return
        
        self.watch_active = True
        self.watch_thread = threading.Thread(target=self._watch_nodes, daemon=True)
        self.watch_thread.start()
        logger.info("Started watching node events")
    
    def stop_watching(self):
        """Stop watching for node events."""
        self.watch_active = False
        if self.watch_thread and self.watch_thread.is_alive():
            self.watch_thread.join(timeout=5)
        logger.info("Stopped watching node events")
    
    def _watch_nodes(self):
        """Watch for node events and trigger callbacks."""
        w = watch.Watch()
        
        while self.watch_active:
            try:
                logger.info("Starting node watch stream")
                
                for event in w.stream(self.v1.list_node, timeout_seconds=60):
                    if not self.watch_active:
                        break
                    
                    event_type = event['type']
                    node = event['object']
                    
                    node_info = self._extract_node_info(node)
                    
                    logger.info("Node event received", 
                              event_type=event_type,
                              node_name=node_info.name,
                              has_all_deletion_taints=bool(node_info.deletion_taints))
                    
                    # Update cache
                    old_node = self.node_cache.get(node_info.name)
                    self.node_cache[node_info.name] = node_info
                    
                    # Trigger callbacks for relevant events
                    if self._should_trigger_callback(event_type, old_node, node_info):
                        for callback in self.event_callbacks:
                            try:
                                callback(event_type, node_info)
                            except Exception as e:
                                logger.error("Error in event callback", 
                                           callback=callback.__name__, 
                                           error=str(e))
                
            except ApiException as e:
                if self.watch_active:
                    logger.error("API error in node watch", 
                               status=e.status, reason=e.reason)
                    time.sleep(5)  # Back off before retrying
            except Exception as e:
                if self.watch_active:
                    logger.error("Unexpected error in node watch", error=str(e))
                    time.sleep(5)
            finally:
                w.stop()
    
    def _should_trigger_callback(self, event_type: str, old_node: Optional[NodeInfo], 
                               new_node: NodeInfo) -> bool:
        """Determine if we should trigger callbacks for this event."""
        # Always trigger for new nodes with ALL deletion taints
        if event_type == "ADDED" and new_node.deletion_taints:
            return True
        
        # Trigger if ALL deletion taints were added or changed
        if event_type == "MODIFIED" and old_node:
            old_has_all_taints = bool(old_node.deletion_taints)
            new_has_all_taints = bool(new_node.deletion_taints)
            
            # Trigger if the node now has all required deletion taints
            if not old_has_all_taints and new_has_all_taints:
                return True
        
        # Trigger for deleted nodes that had ALL deletion taints
        if event_type == "DELETED" and old_node and old_node.deletion_taints:
            return True
        
        return False
    
    def get_services_with_dns_annotations(self) -> List[ServiceDNSConfig]:
        """Get services that have Epictetus DNS management annotations."""
        try:
            services = self.v1.list_service_for_all_namespaces()
            dns_configs = []
            
            for service in services.items:
                if not service.metadata.annotations:
                    continue
                
                # Try to create DNS config from annotations
                dns_config = ServiceDNSConfig.from_service_annotations(
                    service_name=service.metadata.name,
                    service_namespace=service.metadata.namespace,
                    annotations=service.metadata.annotations
                )
                
                if dns_config:
                    dns_configs.append(dns_config)
                    logger.debug("Found service with DNS config", 
                               service=f"{dns_config.service_namespace}/{dns_config.service_name}",
                               hostname=dns_config.hostname,
                               ttl=dns_config.ttl,
                               proxied=dns_config.proxied)
            
            logger.info("Found services with Epictetus DNS annotations", count=len(dns_configs))
            return dns_configs
            
        except ApiException as e:
            logger.error("Kubernetes API error getting services", 
                        status=e.status, reason=e.reason)
            raise
        except Exception as e:
            logger.error("Unexpected error getting services with DNS annotations", error=str(e))
            raise
    
    def health_check(self) -> Dict[str, any]:
        """Perform health check on Kubernetes connectivity."""
        try:
            # Try to get cluster version
            version = self.v1.get_api_resources()
            
            # Check if we can list nodes
            nodes = self.v1.list_node(limit=1)
            
            return {
                'status': 'healthy',
                'api_accessible': True,
                'nodes_accessible': True,
                'cached_nodes': len(self.node_cache),
                'watch_active': self.watch_active
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'api_accessible': False,
                'watch_active': self.watch_active
            } 