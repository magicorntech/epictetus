#!/usr/bin/env python3
"""
Epictetus - A poor man's load balancer
A standalone service that monitors Kubernetes nodes and manages CloudFlare DNS records.
"""

import os
import sys
import signal
import threading
import time
from app import create_app
from app.config import Config
from app.logger import get_logger

logger = get_logger(__name__)

def main():
    """Main application entry point."""
    try:
        # Validate configuration
        errors = Config.validate()
        if errors:
            print("Configuration validation failed:")
            for error in errors:
                print(f"  - {error}")
            sys.exit(1)
        
        # Create Flask application
        app = create_app()
        
        print(f"🏛️  Starting Epictetus - A poor man's load balancer")
        print(f"📋 DNS Hostnames: {', '.join(app.config.get('DNS_HOSTNAMES', []))}")
        print(f"⏱️  Sync Interval: {app.config.get('DNS_SYNC_INTERVAL', 60)} seconds")
        print(f"📊 Log Level: {app.config.get('LOG_LEVEL', 'INFO')}")
        
        # Get configuration for optional health server
        enable_health_server = os.environ.get('ENABLE_HEALTH_SERVER', 'true').lower() == 'true'
        health_port = int(os.environ.get('HEALTH_PORT', '8080'))
        
        # Start minimal health check server in background if enabled
        if enable_health_server:
            health_thread = threading.Thread(
                target=run_health_server,
                args=(app, health_port),
                daemon=True
            )
            health_thread.start()
            print(f"🏥 Health server running on port {health_port}")
        
        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            print(f"\n📡 Received signal {signum}, shutting down gracefully...")
            
            # Stop the DNS manager
            if hasattr(app, 'dns_manager'):
                app.dns_manager.stop()
            
            # Stop the scheduler
            if hasattr(app, 'scheduler'):
                app.scheduler.shutdown(wait=True)
            
            print("✅ Epictetus shutdown complete")
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        print("🔄 Epictetus is now running...")
        print("📡 Watching for Kubernetes node events...")
        print("⚡ Press Ctrl+C to stop")
        
        # Main loop - just keep the service running
        while True:
            time.sleep(1)
        
    except KeyboardInterrupt:
        print("\n🛑 Received interrupt signal, shutting down...")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Failed to start Epictetus: {e}")
        logger.error("Failed to start Epictetus", error=str(e))
        sys.exit(1)


def run_health_server(app, port):
    """Run the health check server in background."""
    try:
        app.run(
            host='0.0.0.0',
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True
        )
    except Exception as e:
        logger.error("Health server failed", error=str(e))


if __name__ == '__main__':
    main() 