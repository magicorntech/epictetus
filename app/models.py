"""Data models for the DNS management service."""

from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class TaintInfo(BaseModel):
    """Model representing a Kubernetes node taint."""
    key: str
    value: str = ""
    effect: str  # NoSchedule, PreferNoSchedule, NoExecute
    
    def __hash__(self):
        return hash((self.key, self.value, self.effect))
    
    def __eq__(self, other):
        if not isinstance(other, TaintInfo):
            return False
        return (self.key, self.value, self.effect) == (other.key, other.value, other.effect)


class NodeInfo(BaseModel):
    """Model representing a Kubernetes node with relevant information."""
    name: str
    external_ip: Optional[str] = None
    taints: List[TaintInfo] = Field(default_factory=list)
    deletion_taints: List[TaintInfo] = Field(default_factory=list)
    labels: Dict[str, str] = Field(default_factory=dict)
    annotations: Dict[str, str] = Field(default_factory=dict)
    ready: bool = False
    creation_timestamp: Optional[datetime] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class DNSRecord(BaseModel):
    """Model representing a CloudFlare DNS record."""
    id: str
    name: str
    type: str = "A"
    content: str  # IP address
    ttl: int = 300
    proxied: bool = False
    zone_id: str
    zone_name: str
    created_on: Optional[datetime] = None
    modified_on: Optional[datetime] = None
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat() if v else None
        }


class DNSManagementEvent(BaseModel):
    """Model representing a DNS management event."""
    event_id: str
    event_type: str  # node_added, node_deleted, dns_record_created, dns_record_deleted
    timestamp: datetime
    node_name: Optional[str] = None
    node_ip: Optional[str] = None
    dns_records: List[DNSRecord] = Field(default_factory=list)
    success: bool = True
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class HealthStatus(BaseModel):
    """Model representing service health status."""
    status: str  # healthy, degraded, unhealthy
    timestamp: datetime
    kubernetes_status: Dict[str, Any] = Field(default_factory=dict)
    cloudflare_status: Dict[str, Any] = Field(default_factory=dict)
    dns_sync_status: Dict[str, Any] = Field(default_factory=dict)
    last_sync: Optional[datetime] = None
    errors: List[str] = Field(default_factory=list)
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SyncReport(BaseModel):
    """Model representing a synchronization report."""
    timestamp: datetime
    nodes_checked: int
    nodes_with_deletion_taints: int
    dns_records_found: int
    dns_records_created: int
    dns_records_deleted: int
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        } 