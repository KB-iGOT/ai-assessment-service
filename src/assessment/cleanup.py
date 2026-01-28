
import os
import shutil
import time
import logging
from pathlib import Path
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import INTERACTIVE_COURSES_PATH

logger = logging.getLogger(__name__)

# Config: Retention Period (Days)
RETENTION_DAYS = int(os.getenv("CLEANUP_RETENTION_DAYS", "7"))

def cleanup_old_files():
    """
    Scans the INTERACTIVE_COURSES_PATH and deletes folders/files
    modified older than RETENTION_DAYS.
    """
    base_path = Path(INTERACTIVE_COURSES_PATH)
    if not base_path.exists():
        logger.warning(f"Cleanup skipped: {base_path} does not exist.")
        return

    logger.info(f"Starting cleanup of {base_path}. Retention: {RETENTION_DAYS} days.")
    
    threshold_time = time.time() - (RETENTION_DAYS * 86400)
    deleted_count = 0
    reclaimed_bytes = 0

    try:
        # Iterate over top-level directories (Course Folders)
        for item in base_path.iterdir():
            try:
                # Check modification time
                mtime = item.stat().st_mtime
                
                if mtime < threshold_time:
                    # Calculate size for logging
                    size = 0
                    if item.is_dir():
                        for root, _, files in os.walk(item):
                            for f in files:
                                size += os.path.getsize(os.path.join(root, f))
                        shutil.rmtree(item)
                    else:
                        size = item.stat().st_size
                        item.unlink()
                        
                    reclaimed_bytes += size
                    deleted_count += 1
                    logger.info(f"Deleted old item: {item.name} (Age: {(time.time() - mtime)/86400:.1f} days)")
            except Exception as e:
                logger.error(f"Error deleting {item.name}: {e}")

        if deleted_count > 0:
            logger.info(f"Cleanup complete. Deleted {deleted_count} items. Reclaimed {reclaimed_bytes / (1024*1024):.2f} MB.")
        else:
            logger.info("Cleanup complete. No items were old enough to delete.")

    except Exception as e:
        logger.exception("Cleanup job failed")

# Global Scheduler Instance
scheduler = AsyncIOScheduler()

def start_cleanup_scheduler():
    """
    Starts the scheduler to run the cleanup job daily.
    """
    # Run everyday at Configured Time (Default: 03:00 AM)
    run_hour = int(os.getenv("CLEANUP_SCHEDULE_HOUR", "3"))
    run_minute = int(os.getenv("CLEANUP_SCHEDULE_MINUTE", "0"))
    
    scheduler.add_job(
        cleanup_old_files, 
        CronTrigger(hour=run_hour, minute=run_minute),
        id="daily_cleanup",
        replace_existing=True
    )
    scheduler.start()
    logger.info(f"Cleanup scheduler started (Schedule: Daily at {run_hour:02d}:{run_minute:02d})")
    
    # Optional: Run once on startup if needed (debated, better not to block startup)
    # cleanup_old_files()

def stop_cleanup_scheduler():
    scheduler.shutdown()
    logger.info("Cleanup scheduler stopped")
