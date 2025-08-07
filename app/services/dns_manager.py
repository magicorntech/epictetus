"""Core DNS management service that orchestrates Kubernetes monitoring and CloudFlare DNS updates."""

import uuid
from datetime import datetime
from typing import List, Dict, Set, Optional
import time

from app.logger import get_logger
from app.models import (
    NodeInfo, DNSRecord, DNSManagementEvent, 
    SyncReport, HealthStatus, ServiceDNSConfig
)
from app.k8s.client import KubernetesClient
from app.cloudflare.client import CloudFlareClient


logger = get_logger(__name__)


class DNSManager:
    """Core service for managing DNS records based on Kubernetes node lifecycle and service annotations."""
    
    def __init__(self, k8s_client: KubernetesClient, cf_client: CloudFlareClient, config):
        """Initialize DNS manager with clients and configuration."""
        self.k8s_client = k8s_client
        self.cf_client = cf_client
        self.config = config
        
        # State tracking
        self.last_sync_time = None
        self.sync_reports = []
        self.events = []
        self.service_configs_cache = []
        
        # Register for node events
        self.k8s_client.register_event_callback(self._handle_node_event)
        
        logger.info("DNS Manager initialized (annotation-based)", 
                   requires_all_taints=True,
                   deletion_taints=config.get('DELETION_TAINTS', []))
    
    def start(self):
        """Start the DNS manager by beginning Kubernetes event watching."""
        try:
            # Load initial service configurations
            self._refresh_service_configs()
            
            # Start watching for node events
            self.k8s_client.start_watching()
            logger.info("DNS Manager started - watching for node events")
        except Exception as e:
            logger.error("Failed to start DNS Manager", error=str(e))
            raise
    
    def stop(self):
        """Stop the DNS manager."""
        try:
            self.k8s_client.stop_watching()
            logger.info("DNS Manager stopped")
        except Exception as e:
            logger.error("Error stopping DNS Manager", error=str(e))
    
    def _refresh_service_configs(self):
        """Refresh service DNS configurations from Kubernetes annotations."""
        try:
            new_configs = self.k8s_client.get_services_with_dns_annotations()
            
            # Log changes
            old_count = len(self.service_configs_cache)
            new_count = len(new_configs)
            
            if old_count != new_count:
                logger.info("Service DNS configurations changed", 
                           old_count=old_count, new_count=new_count)
            
            # Log service details
            for config in new_configs:
                logger.debug("Active DNS config", 
                           service=f"{config.service_namespace}/{config.service_name}",
                           hostname=config.hostname,
                           ttl=config.ttl,
                           proxied=config.proxied)
            
            self.service_configs_cache = new_configs
            
        except Exception as e:
            logger.error("Failed to refresh service configurations", error=str(e))
            # Keep existing configs on error
    
    def _handle_node_event(self, event_type: str, node_info: NodeInfo):
        """Handle Kubernetes node events and update DNS accordingly."""
        event_id = str(uuid.uuid4())
        
        try:
            logger.info("Processing node event", 
                       event_type=event_type, 
                       node_name=node_info.name,
                       external_ip=node_info.external_ip,
                       has_all_deletion_taints=len(node_info.deletion_taints),
                       has_external_ip_label='k8s.magicorn.net/external-ip' in node_info.labels,
                       external_ip_label_value=node_info.labels.get('k8s.magicorn.net/external-ip'),
                       flannel_annotation=node_info.annotations.get('flannel.alpha.coreos.com/public-ip'))
            
            if event_type == "ADDED":
                self._handle_node_added(event_id, node_info)
            elif event_type == "MODIFIED":
                self._handle_node_modified(event_id, node_info)
            elif event_type == "DELETED":
                self._handle_node_deleted(event_id, node_info)
            
        except Exception as e:
            logger.error("Error handling node event", 
                        event_type=event_type, 
                        node_name=node_info.name, 
                        error=str(e))
            
            # Record failed event
            self._record_event(
                event_id=event_id,
                event_type=f"node_{event_type.lower()}_failed",
                node_name=node_info.name,
                node_ip=node_info.external_ip,
                success=False,
                error_message=str(e)
            )
    
    def _handle_node_added(self, event_id: str, node_info: NodeInfo):
        """Handle new node added to cluster."""
        if not node_info.external_ip:
            logger.warning("Node added without external IP", node_name=node_info.name)
            return
        
        # If node has ALL deletion taints, don't add DNS records
        if node_info.deletion_taints:
            logger.info("Node added with all deletion taints - skipping DNS record creation",
                       node_name=node_info.name,
                       taint_keys=[t.key for t in node_info.deletion_taints])
            return
        
        # Refresh service configs in case they changed
        self._refresh_service_configs()
        
        # Add DNS records for the new node based on service configurations
        dns_records = []
        for service_config in self.service_configs_cache:
            try:
                record = self.cf_client.create_dns_record_from_service_config(
                    service_config, node_info.external_ip
                )
                if record:
                    dns_records.append(record)
            except Exception as e:
                logger.error("Failed to add DNS record for new node", 
                           service=f"{service_config.service_namespace}/{service_config.service_name}",
                           hostname=service_config.hostname,
                           node_name=node_info.name, 
                           error=str(e))
        
        self._record_event(
            event_id=event_id,
            event_type="node_added",
            node_name=node_info.name,
            node_ip=node_info.external_ip,
            service_configs=self.service_configs_cache.copy(),
            dns_records=dns_records,
            success=True,
            metadata={"service_configs_count": len(self.service_configs_cache), "records_created": len(dns_records)}
        )
    
    def _handle_node_modified(self, event_id: str, node_info: NodeInfo):
        """Handle node modifications (when ALL deletion taints are now present)."""
        if not node_info.external_ip:
            return
        
        # If node now has ALL deletion taints, remove its DNS records
        if node_info.deletion_taints:
            logger.info("Node now has all deletion taints - removing DNS records",
                       node_name=node_info.name,
                       taint_keys=[t.key for t in node_info.deletion_taints])
            
            # Refresh service configs
            self._refresh_service_configs()
            
            deleted_records = []
            for service_config in self.service_configs_cache:
                try:
                    deleted = self.cf_client.delete_dns_records_by_ip(
                        service_config.hostname, node_info.external_ip
                    )
                    deleted_records.extend(deleted)
                except Exception as e:
                    logger.error("Failed to delete DNS records for modified node", 
                               service=f"{service_config.service_namespace}/{service_config.service_name}",
                               hostname=service_config.hostname,
                               node_name=node_info.name, 
                               error=str(e))
            
            self._record_event(
                event_id=event_id,
                event_type="node_modified_all_deletion_taints",
                node_name=node_info.name,
                node_ip=node_info.external_ip,
                service_configs=self.service_configs_cache.copy(),
                success=True,
                metadata={"records_deleted": len(deleted_records), "deleted_record_ids": deleted_records}
            )
    
    def _handle_node_deleted(self, event_id: str, node_info: NodeInfo):
        """Handle node deletion from cluster."""
        if not node_info.external_ip:
            return
        
        # Refresh service configs
        self._refresh_service_configs()
        
        # Remove all DNS records for this node's IP
        deleted_records = []
        for service_config in self.service_configs_cache:
            try:
                deleted = self.cf_client.delete_dns_records_by_ip(
                    service_config.hostname, node_info.external_ip
                )
                deleted_records.extend(deleted)
            except Exception as e:
                logger.error("Failed to delete DNS records for deleted node", 
                           service=f"{service_config.service_namespace}/{service_config.service_name}",
                           hostname=service_config.hostname,
                           node_name=node_info.name, 
                           error=str(e))
        
        self._record_event(
            event_id=event_id,
            event_type="node_deleted",
            node_name=node_info.name,
            node_ip=node_info.external_ip,
            service_configs=self.service_configs_cache.copy(),
            success=True,
            metadata={"records_deleted": len(deleted_records), "deleted_record_ids": deleted_records}
        )
    
    def perform_full_sync(self) -> SyncReport:
        """Perform a full synchronization of DNS records with current cluster state."""
        start_time = time.time()
        sync_time = datetime.now()
        
        logger.info("Starting full DNS synchronization")
        
        try:
            # Refresh service configurations
            self._refresh_service_configs()
            
            # Get all current nodes
            all_nodes = self.k8s_client.get_all_nodes()
            
            # Analyze all nodes for detailed logging
            nodes_with_external_ip_label = [node for node in all_nodes if 'k8s.magicorn.net/external-ip' in node.labels]
            nodes_with_deletion_taints = [node for node in all_nodes if node.deletion_taints]
            nodes_without_external_ip = [node for node in all_nodes if not node.external_ip]
            
            # Get nodes without ALL deletion taints (these should have DNS records)
            healthy_nodes = [node for node in all_nodes if not node.deletion_taints and node.external_ip]
            healthy_ips = [node.external_ip for node in healthy_nodes]
            
            # Log detailed analysis
            logger.info("Cluster state analysis for sync", 
                       total_nodes=len(all_nodes),
                       nodes_with_external_ip_label=len(nodes_with_external_ip_label),
                       nodes_with_deletion_taints=len(nodes_with_deletion_taints),
                       nodes_without_external_ip=len(nodes_without_external_ip),
                       healthy_nodes=len(healthy_nodes),
                       service_configs=len(self.service_configs_cache))
            
            # Log nodes that have the label but are excluded from DNS
            excluded_labeled_nodes = [node for node in nodes_with_external_ip_label 
                                    if node not in healthy_nodes]
            if excluded_labeled_nodes:
                for node in excluded_labeled_nodes:
                    reason = []
                    if node.deletion_taints:
                        reason.append(f"has_deletion_taints({len(node.deletion_taints)})")
                    if not node.external_ip:
                        reason.append("no_external_ip")
                    if not node.ready:
                        reason.append("not_ready")
                    
                    logger.warning("Node with k8s.magicorn.net/external-ip label excluded from DNS", 
                                 node_name=node.name,
                                 external_ip_label=node.labels.get('k8s.magicorn.net/external-ip'),
                                 resolved_external_ip=node.external_ip,
                                 exclusion_reasons=reason)
            
            # Sync DNS records for each service configuration (delete invalid records)
            sync_results = self.cf_client.sync_dns_records_for_service_configs(
                self.service_configs_cache, healthy_ips
            )
            
            # Create missing DNS records for healthy nodes
            total_created = 0
            creation_errors = []
            
            for service_config in self.service_configs_cache:
                try:
                    # Get current DNS records for this hostname
                    current_records = self.cf_client.get_dns_records(service_config.hostname)
                    current_ips = {record.content for record in current_records}
                    
                    # Find healthy IPs that don't have DNS records
                    missing_ips = set(healthy_ips) - current_ips
                    
                    # Create missing records with service-specific settings
                    for ip in missing_ips:
                        try:
                            record = self.cf_client.create_dns_record_from_service_config(
                                service_config, ip
                            )
                            if record:
                                total_created += 1
                                logger.info("Created missing DNS record during sync", 
                                          service=f"{service_config.service_namespace}/{service_config.service_name}",
                                          hostname=service_config.hostname, 
                                          ip=ip, 
                                          record_id=record.id)
                        except Exception as e:
                            error_msg = f"Failed to create DNS record for {service_config.hostname} -> {ip}: {str(e)}"
                            creation_errors.append(error_msg)
                            logger.error("Failed to create missing DNS record", 
                                       service=f"{service_config.service_namespace}/{service_config.service_name}",
                                       hostname=service_config.hostname, 
                                       ip=ip, error=str(e))
                
                except Exception as e:
                    error_msg = f"Failed to check/create records for {service_config.hostname}: {str(e)}"
                    creation_errors.append(error_msg)
                    logger.error("Error during record creation check", 
                               service=f"{service_config.service_namespace}/{service_config.service_name}",
                               hostname=service_config.hostname, error=str(e))
            
            # Calculate totals
            total_deleted = sum(result.get('records_deleted', 0) for result in sync_results.values())
            total_kept = sum(result.get('records_kept', 0) for result in sync_results.values())
            errors = creation_errors.copy()
            for hostname, result in sync_results.items():
                if not result.get('success', True):
                    errors.append(f"{hostname}: {result.get('error', 'Unknown error')}")
                errors.extend(result.get('errors', []))
            
            # Get current DNS record count
            hostnames = [config.hostname for config in self.service_configs_cache]
            current_dns_records = self.cf_client.get_all_dns_records_for_hostnames(hostnames)
            total_dns_records = sum(len(records) for records in current_dns_records.values())
            
            duration = time.time() - start_time
            
            sync_report = SyncReport(
                timestamp=sync_time,
                nodes_checked=len(all_nodes),
                nodes_with_deletion_taints=len(nodes_with_all_deletion_taints),
                services_checked=len(self.service_configs_cache),
                dns_configs_found=len(self.service_configs_cache),
                dns_records_found=total_dns_records,
                dns_records_created=total_created,
                dns_records_deleted=total_deleted,
                errors=errors,
                duration_seconds=duration
            )
            
            # Store the report
            self.sync_reports.append(sync_report)
            self.last_sync_time = sync_time
            
            # Keep only last 100 reports
            if len(self.sync_reports) > 100:
                self.sync_reports = self.sync_reports[-100:]
            
            logger.info("Completed full DNS synchronization", 
                       duration_seconds=duration,
                       nodes_checked=len(all_nodes),
                       services_checked=len(self.service_configs_cache),
                       records_created=total_created,
                       records_deleted=total_deleted,
                       records_kept=total_kept,
                       errors_count=len(errors))
            
            return sync_report
            
        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"Full sync failed: {str(e)}"
            logger.error("Full synchronization failed", error=str(e), duration_seconds=duration)
            
            sync_report = SyncReport(
                timestamp=sync_time,
                nodes_checked=0,
                nodes_with_deletion_taints=0,
                services_checked=0,
                dns_configs_found=0,
                dns_records_found=0,
                dns_records_created=0,
                dns_records_deleted=0,
                errors=[error_msg],
                duration_seconds=duration
            )
            
            self.sync_reports.append(sync_report)
            return sync_report
    
    def _record_event(self, event_id: str, event_type: str, node_name: str = None, 
                     node_ip: str = None, service_configs: List[ServiceDNSConfig] = None,
                     dns_records: List[DNSRecord] = None, success: bool = True, 
                     error_message: str = None, metadata: Dict = None):
        """Record a DNS management event."""
        event = DNSManagementEvent(
            event_id=event_id,
            event_type=event_type,
            timestamp=datetime.now(),
            node_name=node_name,
            node_ip=node_ip,
            service_configs=service_configs or [],
            dns_records=dns_records or [],
            success=success,
            error_message=error_message,
            metadata=metadata or {}
        )
        
        self.events.append(event)
        
        # Keep only last 1000 events
        if len(self.events) > 1000:
            self.events = self.events[-1000:]
        
        logger.info("Recorded DNS management event", 
                   event_id=event_id, 
                   event_type=event_type, 
                   success=success)
    
    def get_health_status(self) -> HealthStatus:
        """Get current health status of the DNS management service."""
        try:
            # Check Kubernetes connectivity
            k8s_health = self.k8s_client.health_check()
            
            # Check CloudFlare connectivity
            cf_health = self.cf_client.health_check()
            
            # Determine overall status
            if (k8s_health.get('status') == 'healthy' and 
                cf_health.get('status') == 'healthy'):
                overall_status = 'healthy'
            elif (k8s_health.get('api_accessible', False) and 
                  cf_health.get('api_accessible', False)):
                overall_status = 'degraded'
            else:
                overall_status = 'unhealthy'
            
            # Collect errors
            errors = []
            if k8s_health.get('status') != 'healthy':
                errors.append(f"Kubernetes: {k8s_health.get('error', 'Unknown error')}")
            if cf_health.get('status') != 'healthy':
                errors.append(f"CloudFlare: {cf_health.get('error', 'Unknown error')}")
            
            # Get node summary for health status
            try:
                all_nodes = self.k8s_client.get_all_nodes()
                nodes_with_external_ip_label = [node for node in all_nodes if 'k8s.magicorn.net/external-ip' in node.labels]
                healthy_nodes = [node for node in all_nodes if not node.deletion_taints and node.external_ip]
            except Exception as e:
                logger.warning("Could not get node information for health status", error=str(e))
                all_nodes = []
                nodes_with_external_ip_label = []
                healthy_nodes = []
            
            # DNS sync status
            dns_sync_status = {
                'last_sync': self.last_sync_time.isoformat() if self.last_sync_time else None,
                'total_syncs': len(self.sync_reports),
                'recent_errors': len([r for r in self.sync_reports[-10:] if r.errors]) if self.sync_reports else 0,
                'active_service_configs': len(self.service_configs_cache),
                'cluster_summary': {
                    'total_nodes': len(all_nodes),
                    'healthy_nodes': len(healthy_nodes),
                    'nodes_with_external_ip_label': len(nodes_with_external_ip_label),
                    'external_ip_label_coverage': f"{len(nodes_with_external_ip_label)}/{len(all_nodes)}" if all_nodes else "0/0"
                }
            }
            
            return HealthStatus(
                status=overall_status,
                timestamp=datetime.now(),
                kubernetes_status=k8s_health,
                cloudflare_status=cf_health,
                dns_sync_status=dns_sync_status,
                last_sync=self.last_sync_time,
                errors=errors
            )
            
        except Exception as e:
            logger.error("Error getting health status", error=str(e))
            return HealthStatus(
                status='unhealthy',
                timestamp=datetime.now(),
                errors=[f"Health check failed: {str(e)}"]
            )
    
    def get_recent_events(self, limit: int = 50) -> List[DNSManagementEvent]:
        """Get recent DNS management events."""
        return self.events[-limit:] if self.events else []
    
    def get_recent_sync_reports(self, limit: int = 10) -> List[SyncReport]:
        """Get recent synchronization reports."""
        return self.sync_reports[-limit:] if self.sync_reports else []
    
    def get_current_dns_state(self) -> Dict:
        """Get current state of DNS records and cluster nodes."""
        try:
            # Refresh service configs
            self._refresh_service_configs()
            
            # Get current nodes
            all_nodes = self.k8s_client.get_all_nodes()
            healthy_nodes = [node for node in all_nodes if not node.deletion_taints and node.external_ip]
            nodes_with_all_deletion_taints = [node for node in all_nodes if node.deletion_taints]
            nodes_with_external_ip_label = [node for node in all_nodes if 'k8s.magicorn.net/external-ip' in node.labels]
            
            # Get current DNS records
            hostnames = [config.hostname for config in self.service_configs_cache]
            dns_records = self.cf_client.get_all_dns_records_for_hostnames(hostnames)
            
            return {
                'cluster_state': {
                    'total_nodes': len(all_nodes),
                    'healthy_nodes': len(healthy_nodes),
                    'healthy_node_ips': [node.external_ip for node in healthy_nodes],
                    'nodes_with_all_deletion_taints': len(nodes_with_all_deletion_taints),
                    'nodes_with_external_ip_label': len(nodes_with_external_ip_label),
                    'deletion_taint_nodes': [
                        {
                            'name': node.name,
                            'ip': node.external_ip,
                            'taints': [{'key': t.key, 'effect': t.effect} for t in node.deletion_taints]
                        }
                        for node in nodes_with_all_deletion_taints
                    ],
                    'external_ip_label_nodes': [
                        {
                            'name': node.name,
                            'external_ip': node.external_ip,
                            'external_ip_label_value': node.labels.get('k8s.magicorn.net/external-ip'),
                            'flannel_annotation': node.annotations.get('flannel.alpha.coreos.com/public-ip'),
                            'ready': node.ready
                        }
                        for node in nodes_with_external_ip_label
                    ]
                },
                'service_configs': [
                    {
                        'service': f"{config.service_namespace}/{config.service_name}",
                        'hostname': config.hostname,
                        'ttl': config.ttl,
                        'proxied': config.proxied
                    }
                    for config in self.service_configs_cache
                ],
                'dns_state': {
                    'hostnames': hostnames,
                    'records_by_hostname': {
                        hostname: [
                            {
                                'id': record.id,
                                'content': record.content,
                                'ttl': record.ttl,
                                'proxied': record.proxied
                            }
                            for record in records
                        ]
                        for hostname, records in dns_records.items()
                    },
                    'total_records': sum(len(records) for records in dns_records.values())
                }
            }
            
        except Exception as e:
            logger.error("Error getting current DNS state", error=str(e))
            return {
                'error': str(e),
                'cluster_state': {},
                'service_configs': [],
                'dns_state': {}
            } 