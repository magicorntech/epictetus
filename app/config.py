"""Configuration management for Epictetus - A poor man's load balancer."""

import os
from dotenv import load_dotenv
from typing import List, Dict, Any
from app.logger import get_logger

load_dotenv()

logger = get_logger(__name__)


class Config:
    """Base configuration class."""
    
    # Flask settings (minimal, just for health checks)
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    TESTING = False
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    LOG_FORMAT = os.environ.get('LOG_FORMAT', 'console')  # json or console
    
    # Kubernetes settings
    K8S_CONFIG_PATH = os.environ.get('K8S_CONFIG_PATH')  # None for in-cluster config
    K8S_NAMESPACE = os.environ.get('K8S_NAMESPACE', 'default')
    
    # CloudFlare settings
    CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN')
    CLOUDFLARE_ZONE_ID = os.environ.get('CLOUDFLARE_ZONE_ID')
    
    # DNS Management settings
    DNS_HOSTNAMES = os.environ.get('DNS_HOSTNAMES', '').split(',')  # comma-separated hostnames
    DNS_SYNC_INTERVAL = int(os.environ.get('DNS_SYNC_INTERVAL', '60'))  # seconds
    
    # Node taints to monitor
    DELETION_TAINTS = [
        'DeletionCandidateOfClusterAutoscaler',
        'ToBeDeletedByClusterAutoscaler'
    ]
    
    # Monitoring and health
    HEALTH_CHECK_INTERVAL = int(os.environ.get('HEALTH_CHECK_INTERVAL', '30'))
    
    # Retry settings
    MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))
    RETRY_DELAY = int(os.environ.get('RETRY_DELAY', '5'))  # seconds
    
    @classmethod
    def validate(cls) -> List[str]:
        """Validate required configuration."""
        errors = []
        
        if not cls.CLOUDFLARE_API_TOKEN:
            errors.append("CLOUDFLARE_API_TOKEN is required")
        
        if not cls.DNS_HOSTNAMES or cls.DNS_HOSTNAMES == ['']:
            errors.append("DNS_HOSTNAMES is required (comma-separated list)")
        
        return errors


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    LOG_LEVEL = 'DEBUG'


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False
    LOG_LEVEL = 'INFO'


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DNS_SYNC_INTERVAL = 5  # Faster for testing


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
} 


class ConfigurationError(Exception):
    """Configuration validation error."""
    pass


def get_config() -> Dict[str, Any]:
    """Get application configuration from environment variables."""
    config = {
        # CloudFlare Configuration
        'CLOUDFLARE_API_TOKEN': os.getenv('CLOUDFLARE_API_TOKEN'),
        # Note: CLOUDFLARE_ZONE_ID is no longer required - zones are auto-detected
        
        # DNS Configuration
        # NOTE: DNS hostnames are now configured via service annotations
        # Use annotations like: epictetus.io/dns-enabled=true, epictetus.io/hostname=example.com
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
        'SERVICE_VERSION': '1.1.0'  # Updated for multi-zone support
    }
    
    # Validate required configuration
    _validate_config(config)
    
    logger.info("Configuration loaded (multi-zone DNS support)", 
               sync_interval=config['DNS_SYNC_INTERVAL'],
               deletion_taints=config['DELETION_TAINTS'],
               requires_all_taints=True)
    
    return config


def _validate_config(config: Dict[str, Any]) -> None:
    """Validate required configuration values."""
    required_fields = [
        'CLOUDFLARE_API_TOKEN'
        # Note: CLOUDFLARE_ZONE_ID is no longer required
    ]
    
    missing_fields = []
    for field in required_fields:
        if not config.get(field):
            missing_fields.append(field)
    
    if missing_fields:
        raise ConfigurationError(
            f"Missing required configuration: {', '.join(missing_fields)}"
        )
    
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
        'api_token': config['CLOUDFLARE_API_TOKEN']
        # Note: zone_id is no longer in config - auto-detected per hostname
    }


def get_kubernetes_config() -> Dict[str, Any]:
    """Get Kubernetes-specific configuration."""
    config = get_config()
    return {
        'config_path': config['K8S_CONFIG_PATH'],
        'deletion_taints': config['DELETION_TAINTS']
    } 