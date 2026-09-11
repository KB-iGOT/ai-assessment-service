
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
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "assessment.lifecycle.events") # Output
KAFKA_REQUEST_TOPIC = os.getenv("KAFKA_REQUEST_TOPIC", "assessment.request") # Input
KAFKA_GROUP_ID = os.getenv("KAFKA_GROUP_ID", "assessment_worker_group")

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
    Publishes an ASSESSMENT_COMPLETED event to Kafka (Lifecycle).
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
        await producer.send_and_wait(KAFKA_TOPIC, event)
        logger.info(f"Published COMPLETION event for job {job_id}")
    except Exception as e:
        logger.error(f"Failed to publish Kafka event: {e}")

async def send_request_event(payload: Dict[str, Any]):
    """
    Publishes an ASSESSMENT_REQUESTED event to Kafka (Work Queue).
    """
    producer = await get_kafka_producer()
    if not producer:
        logger.warning("Cannot schedule job (Kafka Producer unavailable)")
        # In a real system, you might fallback to DB polling or raise Error
        raise Exception("Kafka Unavailable")

    event = {
        "event_type": "ASSESSMENT_REQUESTED",
        "timestamp": datetime.utcnow().isoformat(),
        "payload": payload
    }

    try:
        # Use job_id as key to ensure ordering (if needed) but random partitions are fine for now
        await producer.send_and_wait(KAFKA_REQUEST_TOPIC, event)
        logger.info(f"Published REQUEST event for job {payload.get('job_id')}")
    except Exception as e:
        logger.error(f"Failed to queue job: {e}")
        raise

# Consumer Factory (For Worker)
from aiokafka import AIOKafkaConsumer

def get_kafka_consumer():
    """Returns a consumer instance for the worker."""
    return AIOKafkaConsumer(
        KAFKA_REQUEST_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=KAFKA_GROUP_ID,
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest'
    )
