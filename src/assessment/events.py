
import os
import json
import logging
import asyncio
from aiokafka import AIOKafkaProducer
from typing import Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Config
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "assessment.lifecycle.events")

_producer = None

async def get_kafka_producer():
    global _producer
    if _producer is None:
        try:
            _producer = AIOKafkaProducer(
                bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
                value_serializer=lambda v: json.dumps(v).encode('utf-8')
            )
            await _producer.start()
            logger.info(f"Kafka Producer connected to {KAFKA_BOOTSTRAP_SERVERS}")
        except Exception as e:
            logger.error(f"Failed to start Kafka Producer: {e}")
            _producer = None
    return _producer

async def stop_kafka_producer():
    global _producer
    if _producer:
        await _producer.stop()
        logger.info("Kafka Producer stopped")
        _producer = None

async def send_completion_event(job_id: str, user_id: str, status: str, result_summary: Dict[str, Any] = None):
    """
    Publishes an ASSESSMENT_COMPLETED event to Kafka.
    """
    producer = await get_kafka_producer()
    if not producer:
        logger.warning("Skipping Kafka event (Producer unavailable)")
        return

    event = {
        "event_type": "ASSESSMENT_GENERATION_COMPLETED",
        "job_id": job_id,
        "user_id": user_id,
        "status": status,
        "timestamp": datetime.utcnow().isoformat(),
        "payload": result_summary or {}
    }

    try:
        # Fire and forget (or await if critical)
        # Using await to ensure it's sent before task finishes
        await producer.send_and_wait(KAFKA_TOPIC, event)
        logger.info(f"Published event for job {job_id} to topic {KAFKA_TOPIC}")
    except Exception as e:
        logger.error(f"Failed to publish Kafka event: {e}")
