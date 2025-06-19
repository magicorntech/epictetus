"""
Epictetus - A poor man's load balancer
A standalone service for managing DNS records based on Kubernetes node lifecycle.
"""

import os
import logging
from flask import Flask
from app.config import Config
from app.logger import setup_logging
from app.k8s.client import KubernetesClient
from app.cloudflare.client import CloudFlareClient
from app.scheduler import create_scheduler


def create_app(config_class=Config):
    """Application factory pattern for creating Flask app."""
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # Setup structured logging
    setup_logging(app.config['LOG_LEVEL'], app.config['LOG_FORMAT'])
    
    # Initialize components
    k8s_client = KubernetesClient(app.config)
    cf_client = CloudFlareClient(app.config)
    
    # Store clients in app context
    app.k8s_client = k8s_client
    app.cf_client = cf_client
    
    # Register minimal health check endpoints
    from app.health import bp as health_bp
    app.register_blueprint(health_bp)
    
    # Initialize scheduler for background tasks
    scheduler = create_scheduler(app)
    app.scheduler = scheduler
    
    # Start background tasks
    if not app.config['TESTING']:
        scheduler.start()
    
    return app 