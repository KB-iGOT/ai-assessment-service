import asyncpg
import json
import logging
from typing import Optional, Dict, Any, List
from .config import DATABASE_URL

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS interactive_assessments (
    course_id TEXT PRIMARY KEY,
    user_id TEXT, -- Nullable for v1 compatibility
    status TEXT NOT NULL, -- 'PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED'
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB,
    assessment_data JSONB,
    token_usage JSONB,
    error_message TEXT
);
"""

def _json_encoder(value):
    return json.dumps(value)

def _json_decoder(value):
    return json.loads(value)

async def _init_connection(conn):
    for type_name in ['json', 'jsonb']:
        await conn.set_type_codec(
            type_name,
            encoder=_json_encoder,
            decoder=_json_decoder,
            schema='pg_catalog'
        )

async def init_db():
    global _pool

    # Idempotency: skip if already initialized
    if _pool is not None:
        return

    _pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=5,
        max_size=20,
        max_inactive_connection_lifetime=1800,  # recycle idle connections after 30 min
        timeout=30,                              # raise if no connection available within 30s
        init=_init_connection,
    )
    logger.info("DB connection pool created (min=5, max=20, recycle=1800s, timeout=30s)")

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(CREATE_TABLE_SQL)
            await conn.execute(
                "ALTER TABLE interactive_assessments ADD COLUMN IF NOT EXISTS user_id TEXT;"
            )
    logger.info("DB schema verified")

async def close_db():
    global _pool
    if _pool:
        await _pool.close()
        logger.info("DB connection pool closed")

def get_pool() -> asyncpg.Pool:
    if not _pool:
        raise RuntimeError("DB pool not initialized — call init_db() at startup")
    return _pool

async def get_assessment_status(course_id: str) -> Optional[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM interactive_assessments WHERE course_id = $1", course_id
        )
        return dict(row) if row else None

async def create_job(course_id: str, user_id: Optional[str] = None):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO interactive_assessments (course_id, user_id, status, updated_at)
                VALUES ($1, $2, 'PENDING', NOW())
                ON CONFLICT (course_id) DO UPDATE
                SET status = 'PENDING', user_id = EXCLUDED.user_id, updated_at = NOW(), error_message = NULL
            """, course_id, user_id)

async def update_job_status(course_id: str, status: str, error: str | None = None):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE interactive_assessments
                SET status = $2, error_message = $3, updated_at = NOW()
                WHERE course_id = $1
            """, course_id, status, error)

async def save_assessment_result(course_id: str, metadata: dict, assessment: dict, usage: dict):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                UPDATE interactive_assessments
                SET status = 'COMPLETED',
                    metadata = $2,
                    assessment_data = $3,
                    token_usage = $4,
                    updated_at = NOW()
                WHERE course_id = $1
            """, course_id, metadata, assessment, usage)

async def find_job_by_prefix(prefix: str) -> Optional[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("""
            SELECT * FROM interactive_assessments
            WHERE course_id LIKE $1 || '%'
            AND status = 'COMPLETED'
            ORDER BY updated_at DESC
            LIMIT 1
        """, prefix)
        return dict(row) if row else None

async def create_completed_job(course_id: str, user_id: str, metadata: dict, assessment: dict, usage: dict):
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            await conn.execute("""
                INSERT INTO interactive_assessments
                (course_id, user_id, status, metadata, assessment_data, token_usage, updated_at)
                VALUES ($1, $2, 'COMPLETED', $3, $4, $5, NOW())
                ON CONFLICT (course_id) DO NOTHING
            """, course_id, user_id, metadata, assessment, usage)

async def update_job_result(job_id: str, user_id: str, new_assessment_data: dict) -> bool:
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            result = await conn.execute("""
                UPDATE interactive_assessments
                SET assessment_data = $3, updated_at = NOW()
                WHERE course_id = $1 AND user_id = $2
            """, job_id, user_id, new_assessment_data)
            return result != "UPDATE 0"

async def get_user_assessments_history(user_id: str) -> List[Dict[str, Any]]:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("""
            SELECT course_id as job_id, status, created_at, updated_at, metadata, assessment_data, error_message
            FROM interactive_assessments
            WHERE user_id = $1
            ORDER BY updated_at DESC
        """, user_id)
        return [dict(row) for row in rows]
