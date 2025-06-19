"""Background scheduler configuration for DNS synchronization tasks."""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
import atexit

from app.logger import get_logger
from app.services.dns_manager import DNSManager


logger = get_logger(__name__)


def create_scheduler(app):
    """Create and configure the background scheduler."""
    
    # Configure job stores and executors
    jobstores = {
        'default': MemoryJobStore()
    }
    
    executors = {
        'default': ThreadPoolExecutor(max_workers=2)
    }
    
    job_defaults = {
        'coalesce': True,  # Combine multiple pending jobs into one
        'max_instances': 1,  # Only allow one instance of each job to run
        'misfire_grace_time': 30  # Allow 30 seconds grace for missed jobs
    }
    
    scheduler = BackgroundScheduler(
        jobstores=jobstores,
        executors=executors,
        job_defaults=job_defaults,
        timezone='UTC'
    )
    
    # Create DNS manager instance
    dns_manager = DNSManager(
        k8s_client=app.k8s_client,
        cf_client=app.cf_client,
        config=app.config
    )
    
    app.dns_manager = dns_manager
    
    # Start DNS manager (event watching)
    dns_manager.start()
    
    # Add scheduled jobs
    sync_interval = app.config.get('DNS_SYNC_INTERVAL', 60)
    
    # Full synchronization job
    scheduler.add_job(
        func=perform_scheduled_sync,
        trigger=IntervalTrigger(seconds=sync_interval),
        args=[dns_manager],
        id='dns_full_sync',
        name='DNS Full Synchronization',
        replace_existing=True
    )
    
    # Health check job (every 30 seconds)
    health_interval = app.config.get('HEALTH_CHECK_INTERVAL', 30)
    scheduler.add_job(
        func=perform_health_check,
        trigger=IntervalTrigger(seconds=health_interval),
        args=[dns_manager],
        id='health_check',
        name='Health Check',
        replace_existing=True
    )
    
    # Cleanup job (every hour)
    scheduler.add_job(
        func=perform_cleanup,
        trigger=IntervalTrigger(hours=1),
        args=[dns_manager],
        id='cleanup',
        name='Cleanup Old Data',
        replace_existing=True
    )
    
    # Ensure scheduler shuts down on app exit
    atexit.register(lambda: shutdown_scheduler(scheduler, dns_manager))
    
    logger.info("Scheduler configured", 
               sync_interval=sync_interval, 
               health_interval=health_interval)
    
    return scheduler


def perform_scheduled_sync(dns_manager: DNSManager):
    """Perform scheduled full DNS synchronization."""
    try:
        logger.info("Starting scheduled DNS synchronization")
        sync_report = dns_manager.perform_full_sync()
        
        logger.info("Completed scheduled DNS synchronization",
                   duration_seconds=sync_report.duration_seconds,
                   nodes_checked=sync_report.nodes_checked,
                   records_deleted=sync_report.dns_records_deleted,
                   errors_count=len(sync_report.errors))
        
        if sync_report.errors:
            logger.warning("DNS synchronization completed with errors", 
                          errors=sync_report.errors)
        
    except Exception as e:
        logger.error("Scheduled DNS synchronization failed", error=str(e))


def perform_health_check(dns_manager: DNSManager):
    """Perform scheduled health check."""
    try:
        health_status = dns_manager.get_health_status()
        
        if health_status.status != 'healthy':
            logger.warning("Health check indicates issues", 
                          status=health_status.status,
                          errors=health_status.errors)
        else:
            logger.debug("Health check passed", status=health_status.status)
        
    except Exception as e:
        logger.error("Health check failed", error=str(e))


def perform_cleanup(dns_manager: DNSManager):
    """Perform cleanup of old data."""
    try:
        # The DNSManager already limits the size of events and sync_reports lists
        # This job can be extended to perform additional cleanup tasks
        
        recent_events_count = len(dns_manager.get_recent_events(1000))
        recent_reports_count = len(dns_manager.get_recent_sync_reports(100))
        
        logger.info("Cleanup completed", 
                   events_count=recent_events_count,
                   reports_count=recent_reports_count)
        
    except Exception as e:
        logger.error("Cleanup failed", error=str(e))


def shutdown_scheduler(scheduler, dns_manager: DNSManager):
    """Gracefully shutdown scheduler and DNS manager."""
    try:
        logger.info("Shutting down scheduler and DNS manager")
        
        # Stop DNS manager first
        dns_manager.stop()
        
        # Then shutdown scheduler
        scheduler.shutdown(wait=True)
        
        logger.info("Scheduler and DNS manager shutdown complete")
        
    except Exception as e:
        logger.error("Error during shutdown", error=str(e)) 