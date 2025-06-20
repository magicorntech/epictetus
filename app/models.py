"""Data models for Epictetus DNS Manager."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Any
from enum import Enum


@dataclass
class TaintInfo:
    """Information about a Kubernetes node taint."""
    key: str
    value: str
    effect: str


@dataclass
class NodeInfo:
    """Information about a Kubernetes node."""
    name: str
    external_ip: Optional[str]
    taints: List[TaintInfo]
    deletion_taints: List[TaintInfo]  # Only taints that indicate deletion
    labels: Dict[str, str]
    annotations: Dict[str, str]
    ready: bool
    creation_timestamp: datetime


@dataclass
class DNSRecord:
    """CloudFlare DNS record information."""
    id: str
    name: str
    content: str
    type: str
    ttl: int
    proxied: bool
    zone_id: str
    zone_name: str
    created_on: datetime
    modified_on: datetime


@dataclass
class ServiceDNSConfig:
    """DNS configuration extracted from service annotations."""
    service_name: str
    service_namespace: str
    hostname: str
    ttl: int = 300
    proxied: bool = False
    enabled: bool = True
    
    @classmethod
    def from_service_annotations(cls, service_name: str, service_namespace: str, 
                               annotations: Dict[str, str]) -> Optional['ServiceDNSConfig']:
        """Create DNS config from service annotations."""
        # Check if Epictetus DNS management is enabled
        if annotations.get('epictetus.io/dns-enabled', 'false').lower() != 'true':
            return None
        
        # Get required hostname
        hostname = annotations.get('epictetus.io/hostname')
        if not hostname:
            return None
        
        # Parse optional settings
        ttl = int(annotations.get('epictetus.io/ttl', '300'))
        proxied = annotations.get('epictetus.io/proxied', 'false').lower() == 'true'
        
        return cls(
            service_name=service_name,
            service_namespace=service_namespace,
            hostname=hostname,
            ttl=ttl,
            proxied=proxied,
            enabled=True
        )


@dataclass
class DNSManagementEvent:
    """Event record for DNS management operations."""
    event_id: str
    event_type: str
    timestamp: datetime
    node_name: Optional[str] = None
    node_ip: Optional[str] = None
    service_configs: List[ServiceDNSConfig] = None
    dns_records: List[DNSRecord] = None
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.service_configs is None:
            self.service_configs = []
        if self.dns_records is None:
            self.dns_records = []
        if self.metadata is None:
            self.metadata = {}


@dataclass
class SyncReport:
    """Report for DNS synchronization operations."""
    timestamp: datetime
    nodes_checked: int
    nodes_with_deletion_taints: int
    services_checked: int
    dns_configs_found: int
    dns_records_found: int
    dns_records_created: int
    dns_records_deleted: int
    errors: List[str]
    duration_seconds: float


@dataclass
class HealthStatus:
    """Health status of the DNS management service."""
    status: str  # healthy, degraded, unhealthy
    timestamp: datetime
    kubernetes_status: Dict[str, Any] = None
    cloudflare_status: Dict[str, Any] = None
    dns_sync_status: Dict[str, Any] = None
    last_sync: Optional[datetime] = None
    errors: List[str] = None
    
    def __post_init__(self):
        if self.kubernetes_status is None:
            self.kubernetes_status = {}
        if self.cloudflare_status is None:
            self.cloudflare_status = {}
        if self.dns_sync_status is None:
            self.dns_sync_status = {}
        if self.errors is None:
            self.errors = [] 