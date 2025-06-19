"""Configuration management for Epictetus DNS Manager."""

import os
from typing import List, Dict, Any
from app.logger import get_logger

logger = get_logger(__name__)


class ConfigurationError(Exception):
    """Configuration validation error."""
    pass


def get_config() -> Dict[str, Any]:
    """Get application configuration from environment variables."""
    config = {
        # CloudFlare Configuration
        'CLOUDFLARE_API_TOKEN': os.getenv('CLOUDFLARE_API_TOKEN'),
        'CLOUDFLARE_ZONE_ID': os.getenv('CLOUDFLARE_ZONE_ID'),
        
        # DNS Configuration
        'DNS_HOSTNAMES': _parse_hostnames(os.getenv('DNS_HOSTNAMES', '')),
        'DNS_SYNC_INTERVAL': int(os.getenv('DNS_SYNC_INTERVAL', '60')),
        
        # Kubernetes Configuration
        'K8S_CONFIG_PATH': os.getenv('K8S_CONFIG_PATH'),  # None = in-cluster config
        
        # Deletion Taints - BOTH must be present for DNS record removal
        'DELETION_TAINTS': [
            'DeletionCandidateOfClusterAutoscaler',
            'ToBeDeletedByClusterAutoscaler'
        ],
        
        # Logging Configuration
        'LOG_LEVEL': os.getenv('LOG_LEVEL', 'INFO'),
        'LOG_FORMAT': os.getenv('LOG_FORMAT', 'console'),  # console or json
        
        # Health Check Configuration
        'HEALTH_CHECK_INTERVAL': int(os.getenv('HEALTH_CHECK_INTERVAL', '30')),
        'ENABLE_HEALTH_SERVER': os.getenv('ENABLE_HEALTH_SERVER', 'true').lower() == 'true',
        'HEALTH_PORT': int(os.getenv('HEALTH_PORT', '8080')),
        
        # Retry Configuration
        'MAX_RETRIES': int(os.getenv('MAX_RETRIES', '3')),
        'RETRY_DELAY': int(os.getenv('RETRY_DELAY', '5')),
        
        # Service Configuration
        'SERVICE_NAME': 'epictetus',
        'SERVICE_VERSION': '1.0.0'
    }
    
    # Validate required configuration
    _validate_config(config)
    
    logger.info("Configuration loaded", 
               hostnames=config['DNS_HOSTNAMES'],
               sync_interval=config['DNS_SYNC_INTERVAL'],
               deletion_taints=config['DELETION_TAINTS'],
               requires_all_taints=True)
    
    return config


def _parse_hostnames(hostnames_str: str) -> List[str]:
    """Parse comma-separated hostnames into a list."""
    if not hostnames_str:
        return []
    
    hostnames = [hostname.strip() for hostname in hostnames_str.split(',')]
    return [hostname for hostname in hostnames if hostname]


def _validate_config(config: Dict[str, Any]) -> None:
    """Validate required configuration values."""
    required_fields = [
        'CLOUDFLARE_API_TOKEN',
        'CLOUDFLARE_ZONE_ID',
        'DNS_HOSTNAMES'
    ]
    
    missing_fields = []
    for field in required_fields:
        if not config.get(field):
            missing_fields.append(field)
    
    if missing_fields:
        raise ConfigurationError(
            f"Missing required configuration: {', '.join(missing_fields)}"
        )
    
    # Validate hostnames format
    if not isinstance(config['DNS_HOSTNAMES'], list) or not config['DNS_HOSTNAMES']:
        raise ConfigurationError("DNS_HOSTNAMES must be a non-empty comma-separated list")
    
    # Validate intervals
    if config['DNS_SYNC_INTERVAL'] < 10:
        raise ConfigurationError("DNS_SYNC_INTERVAL must be at least 10 seconds")
    
    if config['HEALTH_CHECK_INTERVAL'] < 5:
        raise ConfigurationError("HEALTH_CHECK_INTERVAL must be at least 5 seconds")
    
    logger.info("Configuration validation successful")


def get_cloudflare_config() -> Dict[str, str]:
    """Get CloudFlare-specific configuration."""
    config = get_config()
    return {
        'api_token': config['CLOUDFLARE_API_TOKEN'],
        'zone_id': config['CLOUDFLARE_ZONE_ID']
    }


def get_kubernetes_config() -> Dict[str, Any]:
    """Get Kubernetes-specific configuration."""
    config = get_config()
    return {
        'config_path': config['K8S_CONFIG_PATH'],
        'deletion_taints': config['DELETION_TAINTS']
    } 