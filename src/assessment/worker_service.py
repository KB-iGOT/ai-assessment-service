
import asyncio
import logging
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import shutil

# Reuse existing modules
from .db import init_db, update_job_status, save_assessment_result, get_assessment_status
from .fetcher import fetch_course_data
from .generator import generate_assessment
from .events import get_kafka_consumer, send_completion_event, stop_kafka_producer
from .config import INTERACTIVE_COURSES_PATH

log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] [WORKER] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_dir / "worker.log", encoding="utf-8")
    ]
)
logger = logging.getLogger(__name__)

async def process_job(payload: Dict[str, Any]):
    """
    Core Logic: Fetches content and runs LLM generation.
    """
    job_id = payload.get('job_id')
    user_id = payload.get('user_id') # Passed from API
    course_ids = payload.get('course_ids', [])
    
    logger.info(f"Starting processing for Job: {job_id}")
    logger.info(f"Incoming Payload: {payload}")
    
    try:
        # 1. Update Status
        await update_job_status(job_id, "IN_PROGRESS")
        
        # 2. Setup Paths
        base_path = Path(INTERACTIVE_COURSES_PATH)
        saved_files = [] 
        # Note: If API saved file uploads to disk, they are available since we share the volume.
        # We need to handle 'extra_files' paths sent in payload.
        extra_files_str = payload.get('extra_files', []) # List of strings (paths)
        extra_files = [Path(p) for p in extra_files_str]
        
        # 3. Fetch Data (Deep Search)
        for cid in course_ids:
            success = await fetch_course_data(cid, base_path)
            if not success:
                logger.warning(f"Failed to fetch content for {cid}")

        # 4. Generate with LLM
        # Reconstruct params from payload
        metadata, assessment, usage = await generate_assessment(
            course_ids=course_ids,
            assessment_type=payload.get('assessment_type'), 
            difficulty_level=payload.get('difficulty'), 
            total_questions=payload.get('total_questions'),
            question_type_counts=payload.get('question_type_counts'),
            additional_instructions=payload.get('additional_instructions'),
            input_language=payload.get('language'),
            topic_names=payload.get('topic_names'),
            blooms_distribution=payload.get('blooms_distribution'),
            enable_blooms=payload.get('enable_blooms', True),
            course_weightage=payload.get('course_weightage'),
            time_limit=payload.get('time_limit'),
            extra_files=extra_files
        )
        
        # Inject API Configuration into Metadata
        metadata['config'] = {
            "assessment_type": payload.get('assessment_type'),
            "difficulty": payload.get('difficulty'),
            "total_questions": payload.get('total_questions'),
            "question_type_counts": payload.get('question_type_counts'),
            "language": payload.get('language'),
            "time_limit": payload.get('time_limit'),
            "course_weightage": payload.get('course_weightage')
        }
        
        # 5. Save Result
        await save_assessment_result(job_id, metadata, assessment, usage)
        
        # 6. Notify Completion
        await send_completion_event(job_id, user_id, "COMPLETED", {"course_ids": course_ids})
        logger.info(f"Job {job_id} Completed Successfully")
        
    except Exception as e:
        logger.exception(f"Job {job_id} Failed: {e}")
        await update_job_status(job_id, "FAILED", str(e))
        await send_completion_event(job_id, user_id, "FAILED", {"error": str(e)})

async def run_worker():
    logger.info("Starting Worker Service...")
    
    # Init DB Connection
    await init_db()
    
    consumer = get_kafka_consumer()
    await consumer.start()
    logger.info("Kafka Consumer Started. Listening for tasks...")
    
    try:
        async for msg in consumer:
            logger.info(f"Received Task: {msg.key}")
            try:
                payload = msg.value.get('payload')
                if payload:
                    await process_job(payload)
                else:
                    logger.warning("Received empty payload")
            except Exception as e:
                logger.error(f"Error processing message: {e}")
            finally:
                # Manual Commit (if we disable auto-commit) or reliance on auto-commit
                pass
    except KeyboardInterrupt:
        logger.info("Stopping Worker...")
    finally:
        await consumer.stop()
        await stop_kafka_producer()
        logger.info("Worker Stopped")

if __name__ == "__main__":
    asyncio.run(run_worker())
