"""Configuration management for Epictetus - A poor man's load balancer."""

import os
from dotenv import load_dotenv
from typing import List, Dict, Any

load_dotenv()


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
        
        if not cls.CLOUDFLARE_ZONE_ID:
            errors.append("CLOUDFLARE_ZONE_ID is required")
        
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