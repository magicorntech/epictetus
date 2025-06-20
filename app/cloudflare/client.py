"""CloudFlare client for managing DNS records across multiple zones."""

import time
from typing import Dict, List, Optional
from datetime import datetime
import CloudFlare
from retrying import retry

from app.logger import get_logger
from app.models import DNSRecord, ServiceDNSConfig


logger = get_logger(__name__)


class CloudFlareClient:
    """Production-ready CloudFlare client for DNS management across multiple zones."""
    
    def __init__(self, app_config):
        """Initialize CloudFlare client."""
        self.config = app_config
        self.cf = None
        self.zones_cache = {}  # Cache zone info {zone_name: zone_id}
        self.hostname_to_zone = {}  # Cache hostname to zone mapping
        
        # Initialize CloudFlare client
        self._init_client()
        
        logger.info("CloudFlare client initialized (multi-zone support)")
    
    def _init_client(self):
        """Initialize CloudFlare client with API token."""
        try:
            token = self.config.get('CLOUDFLARE_API_TOKEN')
            if not token:
                raise ValueError("CLOUDFLARE_API_TOKEN is required")
            
            self.cf = CloudFlare.CloudFlare(token=token)
            
            # Load all available zones
            self._refresh_zones_cache()
            
            logger.info("CloudFlare client authenticated", 
                       available_zones=len(self.zones_cache))
            
        except Exception as e:
            logger.error("Failed to initialize CloudFlare client", error=str(e))
            raise
    
    def _refresh_zones_cache(self):
        """Refresh the cache of available zones."""
        try:
            zones = self.cf.zones.get()
            self.zones_cache = {zone['name']: zone['id'] for zone in zones}
            
            logger.debug("Refreshed zones cache", 
                        zones=list(self.zones_cache.keys()))
            
        except Exception as e:
            logger.error("Failed to refresh zones cache", error=str(e))
            raise
    
    def _get_zone_for_hostname(self, hostname: str) -> Optional[str]:
        """Determine which zone a hostname belongs to."""
        # Check cache first
        if hostname in self.hostname_to_zone:
            return self.hostname_to_zone[hostname]
        
        # Extract domain from hostname (e.g., api.example.com -> example.com)
        parts = hostname.split('.')
        
        # Try different domain combinations (handles subdomains)
        for i in range(len(parts)):
            domain = '.'.join(parts[i:])
            if domain in self.zones_cache:
                zone_id = self.zones_cache[domain]
                self.hostname_to_zone[hostname] = zone_id
                logger.debug("Found zone for hostname", 
                           hostname=hostname, domain=domain, zone_id=zone_id)
                return zone_id
        
        # Zone not found
        logger.warning("No zone found for hostname", 
                      hostname=hostname, 
                      available_zones=list(self.zones_cache.keys()))
        return None
    
    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def get_dns_records(self, hostname: str) -> List[DNSRecord]:
        """Get all A records for a specific hostname."""
        try:
            zone_id = self._get_zone_for_hostname(hostname)
            if not zone_id:
                raise ValueError(f"No CloudFlare zone found for hostname: {hostname}")
            
            records = self.cf.zones.dns_records.get(
                zone_id,
                params={'name': hostname, 'type': 'A'}
            )
            
            dns_records = []
            # Get zone name from cache
            zone_name = None
            for cached_zone_name, cached_zone_id in self.zones_cache.items():
                if cached_zone_id == zone_id:
                    zone_name = cached_zone_name
                    break
            
            for record in records:
                dns_record = DNSRecord(
                    id=record['id'],
                    name=record['name'],
                    type=record['type'],
                    content=record['content'],
                    ttl=record['ttl'],
                    proxied=record['proxied'],
                    zone_id=zone_id,  # Use the zone_id we already have
                    zone_name=zone_name or 'unknown',  # Use cached zone name
                    created_on=datetime.fromisoformat(record['created_on'].replace('Z', '+00:00')),
                    modified_on=datetime.fromisoformat(record['modified_on'].replace('Z', '+00:00'))
                )
                dns_records.append(dns_record)
            
            logger.info("Retrieved DNS records", 
                       hostname=hostname, zone_id=zone_id, count=len(dns_records))
            return dns_records
            
        except CloudFlare.exceptions.CloudFlareAPIError as e:
            logger.error("CloudFlare API error getting DNS records", 
                        hostname=hostname, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error getting DNS records", 
                        hostname=hostname, error=str(e))
            raise
    
    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def create_dns_record(self, hostname: str, ip_address: str, ttl: int = 300, 
                         proxied: bool = False) -> DNSRecord:
        """Create a new DNS A record."""
        try:
            zone_id = self._get_zone_for_hostname(hostname)
            if not zone_id:
                raise ValueError(f"No CloudFlare zone found for hostname: {hostname}")
            
            data = {
                'type': 'A',
                'name': hostname,
                'content': ip_address,
                'ttl': ttl,
                'proxied': proxied
            }
            
            result = self.cf.zones.dns_records.post(zone_id, data=data)
            
            # Get zone name from cache
            zone_name = None
            for cached_zone_name, cached_zone_id in self.zones_cache.items():
                if cached_zone_id == zone_id:
                    zone_name = cached_zone_name
                    break
            
            dns_record = DNSRecord(
                id=result['id'],
                name=result['name'],
                content=result['content'],
                type=result['type'],
                ttl=result['ttl'],
                proxied=result['proxied'],
                zone_id=zone_id,  # Use the zone_id we already have
                zone_name=zone_name or 'unknown',  # Use cached zone name
                created_on=datetime.fromisoformat(result['created_on'].replace('Z', '+00:00')),
                modified_on=datetime.fromisoformat(result['modified_on'].replace('Z', '+00:00'))
            )
            
            logger.info("Created DNS record", 
                       hostname=hostname, ip_address=ip_address, 
                       zone_id=zone_id, record_id=dns_record.id, 
                       ttl=ttl, proxied=proxied)
            return dns_record
            
        except CloudFlare.exceptions.CloudFlareAPIError as e:
            logger.error("CloudFlare API error creating DNS record", 
                        hostname=hostname, ip_address=ip_address, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error creating DNS record", 
                        hostname=hostname, ip_address=ip_address, error=str(e))
            raise
    
    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def delete_dns_record(self, record_id: str, zone_id: str) -> bool:
        """Delete a DNS record by ID."""
        try:
            self.cf.zones.dns_records.delete(zone_id, record_id)
            
            logger.info("Deleted DNS record", record_id=record_id, zone_id=zone_id)
            return True
            
        except CloudFlare.exceptions.CloudFlareAPIError as e:
            logger.error("CloudFlare API error deleting DNS record", 
                        record_id=record_id, zone_id=zone_id, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error deleting DNS record", 
                        record_id=record_id, zone_id=zone_id, error=str(e))
            raise
    
    def delete_dns_records_by_ip(self, hostname: str, ip_address: str) -> List[str]:
        """Delete all A records for a hostname that point to a specific IP."""
        try:
            # Get all records for the hostname
            records = self.get_dns_records(hostname)
            
            # Filter records by IP address
            matching_records = [r for r in records if r.content == ip_address]
            
            deleted_record_ids = []
            for record in matching_records:
                try:
                    self.delete_dns_record(record.id, record.zone_id)
                    deleted_record_ids.append(record.id)
                except Exception as e:
                    logger.error("Failed to delete DNS record", 
                               record_id=record.id, error=str(e))
            
            logger.info("Deleted DNS records by IP", 
                       hostname=hostname, ip_address=ip_address, 
                       deleted_count=len(deleted_record_ids))
            
            return deleted_record_ids
            
        except Exception as e:
            logger.error("Error deleting DNS records by IP", 
                        hostname=hostname, ip_address=ip_address, error=str(e))
            raise
    
    def sync_dns_records_for_hostnames(self, hostnames: List[str], 
                                     valid_ips: List[str]) -> Dict[str, Dict]:
        """Sync DNS records for multiple hostnames, keeping only valid IPs."""
        results = {}
        
        for hostname in hostnames:
            if not hostname.strip():
                continue
                
            try:
                result = self.sync_dns_records_for_hostname(hostname.strip(), valid_ips)
                results[hostname] = result
            except Exception as e:
                logger.error("Failed to sync DNS records for hostname", 
                           hostname=hostname, error=str(e))
                results[hostname] = {
                    'success': False,
                    'error': str(e),
                    'records_deleted': 0,
                    'records_kept': 0
                }
        
        return results
    
    def sync_dns_records_for_hostname(self, hostname: str, 
                                    valid_ips: List[str]) -> Dict:
        """Sync DNS records for a hostname, removing records for invalid IPs."""
        try:
            # Get all current records
            current_records = self.get_dns_records(hostname)
            
            records_deleted = 0
            records_kept = 0
            errors = []
            
            for record in current_records:
                if record.content not in valid_ips:
                    # Delete record for invalid IP
                    try:
                        self.delete_dns_record(record.id, record.zone_id)
                        records_deleted += 1
                        logger.info("Deleted DNS record for invalid IP", 
                                   hostname=hostname, ip=record.content, 
                                   record_id=record.id, zone_id=record.zone_id)
                    except Exception as e:
                        error_msg = f"Failed to delete record {record.id}: {str(e)}"
                        errors.append(error_msg)
                        logger.error("Failed to delete DNS record", 
                                   record_id=record.id, error=str(e))
                else:
                    records_kept += 1
            
            logger.info("Completed DNS sync for hostname", 
                       hostname=hostname, 
                       records_deleted=records_deleted,
                       records_kept=records_kept,
                       errors_count=len(errors))
            
            return {
                'success': len(errors) == 0,
                'records_deleted': records_deleted,
                'records_kept': records_kept,
                'errors': errors
            }
            
        except Exception as e:
            logger.error("Error syncing DNS records for hostname", 
                        hostname=hostname, error=str(e))
            raise
    
    def add_dns_record_for_new_node(self, hostname: str, ip_address: str) -> Optional[DNSRecord]:
        """Add DNS record for a new node if it doesn't already exist."""
        try:
            # Check if record already exists
            existing_records = self.get_dns_records(hostname)
            for record in existing_records:
                if record.content == ip_address:
                    logger.info("DNS record already exists", 
                               hostname=hostname, ip_address=ip_address)
                    return record
            
            # Create new record
            new_record = self.create_dns_record(hostname, ip_address)
            logger.info("Added DNS record for new node", 
                       hostname=hostname, ip_address=ip_address, 
                       record_id=new_record.id)
            
            return new_record
            
        except Exception as e:
            logger.error("Failed to add DNS record for new node", 
                        hostname=hostname, ip_address=ip_address, error=str(e))
            raise
    
    def get_all_dns_records_for_hostnames(self, hostnames: List[str]) -> Dict[str, List[DNSRecord]]:
        """Get all DNS records for a list of hostnames."""
        results = {}
        
        for hostname in hostnames:
            if not hostname.strip():
                continue
                
            try:
                records = self.get_dns_records(hostname.strip())
                results[hostname] = records
            except Exception as e:
                logger.error("Failed to get DNS records for hostname", 
                           hostname=hostname, error=str(e))
                results[hostname] = []
        
        return results
    
    def health_check(self) -> Dict[str, any]:
        """Perform health check on CloudFlare connectivity."""
        try:
            # Try to refresh zones cache - this tests the core functionality we need
            self._refresh_zones_cache()
            
            # Check if we have zones available
            if not self.zones_cache:
                raise Exception("No zones available or accessible")
            
            return {
                'status': 'healthy',
                'api_accessible': True,
                'available_zones': len(self.zones_cache),
                'zones': list(self.zones_cache.keys())
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'api_accessible': False
            }
    
    def create_dns_record_from_service_config(self, service_config: ServiceDNSConfig, 
                                             ip_address: str) -> Optional[DNSRecord]:
        """Create DNS record based on service configuration."""
        try:
            # Check if record already exists
            existing_records = self.get_dns_records(service_config.hostname)
            for record in existing_records:
                if record.content == ip_address:
                    logger.info("DNS record already exists for service config", 
                               service=f"{service_config.service_namespace}/{service_config.service_name}",
                               hostname=service_config.hostname, 
                               ip_address=ip_address)
                    return record
            
            # Create new record with service-specific settings
            new_record = self.create_dns_record(
                hostname=service_config.hostname,
                ip_address=ip_address,
                ttl=service_config.ttl,
                proxied=service_config.proxied
            )
            
            logger.info("Created DNS record from service config", 
                       service=f"{service_config.service_namespace}/{service_config.service_name}",
                       hostname=service_config.hostname, 
                       ip_address=ip_address,
                       record_id=new_record.id,
                       zone_id=new_record.zone_id,
                       ttl=service_config.ttl,
                       proxied=service_config.proxied)
            
            return new_record
            
        except Exception as e:
            logger.error("Failed to create DNS record from service config", 
                        service=f"{service_config.service_namespace}/{service_config.service_name}",
                        hostname=service_config.hostname, 
                        ip_address=ip_address, 
                        error=str(e))
            raise

    def sync_dns_records_for_service_configs(self, service_configs: List[ServiceDNSConfig], 
                                           valid_ips: List[str]) -> Dict[str, Dict]:
        """Sync DNS records for service configurations, keeping only valid IPs."""
        results = {}
        
        for config in service_configs:
            hostname = config.hostname
            try:
                result = self.sync_dns_records_for_hostname(hostname, valid_ips)
                results[hostname] = result
                results[hostname]['service'] = f"{config.service_namespace}/{config.service_name}"
            except Exception as e:
                logger.error("Failed to sync DNS records for service config", 
                           service=f"{config.service_namespace}/{config.service_name}",
                           hostname=hostname, error=str(e))
                results[hostname] = {
                    'success': False,
                    'error': str(e),
                    'records_deleted': 0,
                    'records_kept': 0,
                    'service': f"{config.service_namespace}/{config.service_name}"
                }
        
        return results 