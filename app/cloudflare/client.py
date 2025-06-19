"""CloudFlare client for managing DNS records."""

import CloudFlare
from typing import List, Dict, Optional, Tuple
from retrying import retry
import time
from datetime import datetime

from app.logger import get_logger
from app.models import DNSRecord


logger = get_logger(__name__)


class CloudFlareClient:
    """Production-ready CloudFlare client for DNS management."""
    
    def __init__(self, app_config):
        """Initialize CloudFlare client."""
        self.config = app_config
        self.cf = None
        self.zone_id = app_config.get('CLOUDFLARE_ZONE_ID')
        self.zone_name = None
        
        # Initialize CloudFlare client
        self._init_client()
        
        logger.info("CloudFlare client initialized", zone_id=self.zone_id)
    
    def _init_client(self):
        """Initialize CloudFlare client with API token."""
        try:
            token = self.config.get('CLOUDFLARE_API_TOKEN')
            if not token:
                raise ValueError("CLOUDFLARE_API_TOKEN is required")
            
            self.cf = CloudFlare.CloudFlare(token=token)
            
            # Verify connection and get zone info
            zone_info = self.cf.zones.get(self.zone_id)
            self.zone_name = zone_info['name']
            
            logger.info("CloudFlare client authenticated", 
                       zone_name=self.zone_name, zone_id=self.zone_id)
            
        except Exception as e:
            logger.error("Failed to initialize CloudFlare client", error=str(e))
            raise
    
    @retry(stop_max_attempt_number=3, wait_fixed=2000)
    def get_dns_records(self, hostname: str) -> List[DNSRecord]:
        """Get all A records for a specific hostname."""
        try:
            records = self.cf.zones.dns_records.get(
                self.zone_id,
                params={'name': hostname, 'type': 'A'}
            )
            
            dns_records = []
            for record in records:
                dns_record = DNSRecord(
                    id=record['id'],
                    name=record['name'],
                    type=record['type'],
                    content=record['content'],
                    ttl=record['ttl'],
                    proxied=record['proxied'],
                    zone_id=record['zone_id'],
                    zone_name=record['zone_name'],
                    created_on=datetime.fromisoformat(record['created_on'].replace('Z', '+00:00')),
                    modified_on=datetime.fromisoformat(record['modified_on'].replace('Z', '+00:00'))
                )
                dns_records.append(dns_record)
            
            logger.info("Retrieved DNS records", 
                       hostname=hostname, count=len(dns_records))
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
    def create_dns_record(self, hostname: str, ip_address: str, ttl: int = 300) -> DNSRecord:
        """Create a new A record for the hostname pointing to the IP address."""
        try:
            record_data = {
                'name': hostname,
                'type': 'A',
                'content': ip_address,
                'ttl': ttl,
                'proxied': False
            }
            
            result = self.cf.zones.dns_records.post(self.zone_id, data=record_data)
            
            dns_record = DNSRecord(
                id=result['id'],
                name=result['name'],
                type=result['type'],
                content=result['content'],
                ttl=result['ttl'],
                proxied=result['proxied'],
                zone_id=result['zone_id'],
                zone_name=result['zone_name'],
                created_on=datetime.fromisoformat(result['created_on'].replace('Z', '+00:00')),
                modified_on=datetime.fromisoformat(result['modified_on'].replace('Z', '+00:00'))
            )
            
            logger.info("Created DNS record", 
                       hostname=hostname, ip_address=ip_address, record_id=dns_record.id)
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
    def delete_dns_record(self, record_id: str) -> bool:
        """Delete a DNS record by ID."""
        try:
            self.cf.zones.dns_records.delete(self.zone_id, record_id)
            
            logger.info("Deleted DNS record", record_id=record_id)
            return True
            
        except CloudFlare.exceptions.CloudFlareAPIError as e:
            logger.error("CloudFlare API error deleting DNS record", 
                        record_id=record_id, error=str(e))
            raise
        except Exception as e:
            logger.error("Unexpected error deleting DNS record", 
                        record_id=record_id, error=str(e))
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
                    self.delete_dns_record(record.id)
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
                        self.delete_dns_record(record.id)
                        records_deleted += 1
                        logger.info("Deleted DNS record for invalid IP", 
                                   hostname=hostname, ip=record.content, 
                                   record_id=record.id)
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
            # Try to get zone info
            zone_info = self.cf.zones.get(self.zone_id)
            
            # Try to get a sample of DNS records
            sample_records = self.cf.zones.dns_records.get(self.zone_id, params={'per_page': 1})
            
            return {
                'status': 'healthy',
                'api_accessible': True,
                'zone_accessible': True,
                'zone_name': zone_info['name'],
                'zone_status': zone_info['status']
            }
        except Exception as e:
            return {
                'status': 'unhealthy',
                'error': str(e),
                'api_accessible': False
            } 