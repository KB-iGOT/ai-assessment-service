import asyncpg
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from .config import DATABASE_URL

logger = logging.getLogger(__name__)

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

async def get_conn() -> asyncpg.Connection:
    conn = await asyncpg.connect(DATABASE_URL)
    for type_name in ['json', 'jsonb']:
        await conn.set_type_codec(
            type_name,
            encoder=json.dumps,
            decoder=json.loads,
            schema='pg_catalog'
        )
    return conn

async def init_db():
    conn: asyncpg.Connection = await get_conn()
    try:
        await conn.execute(CREATE_TABLE_SQL)
        
        # Auto-Migration: Ensure user_id column exists (for existing DBs)
        try:
            await conn.execute("ALTER TABLE interactive_assessments ADD COLUMN IF NOT EXISTS user_id TEXT;")
        except Exception as e:
            # Ignore if column already exists or other harmless race conditions
            pass
            
    finally:
        await conn.close()

async def get_assessment_status(course_id: str) -> Optional[Dict[str, Any]]:
    conn: asyncpg.Connection = await get_conn()
    try:
        row = await conn.fetchrow("SELECT * FROM interactive_assessments WHERE course_id = $1", course_id)
        if row:
            return dict(row)
        return None
    finally:
        await conn.close()

async def create_job(course_id: str, user_id: Optional[str] = None):
    conn: asyncpg.Connection = await get_conn()
    try:
        await conn.execute("""
            INSERT INTO interactive_assessments (course_id, user_id, status, updated_at)
            VALUES ($1, $2, 'PENDING', NOW())
            ON CONFLICT (course_id) DO UPDATE
            SET status = 'PENDING', user_id = EXCLUDED.user_id, updated_at = NOW(), error_message = NULL
        """, course_id, user_id)
    finally:
        await conn.close()

async def update_job_status(course_id: str, status: str, error: str | None = None):
    conn: asyncpg.Connection = await get_conn()
    try:
        await conn.execute("""
            UPDATE interactive_assessments
            SET status = $2, error_message = $3, updated_at = NOW()
            WHERE course_id = $1
        """, course_id, status, error)
    finally:
        await conn.close()

async def save_assessment_result(course_id: str, metadata: dict, assessment: dict, usage: dict):
    conn: asyncpg.Connection = await get_conn()
    try:
        await conn.execute("""
            UPDATE interactive_assessments
            SET status = 'COMPLETED', 
                metadata = $2, 
                assessment_data = $3, 
                token_usage = $4, 
                updated_at = NOW()
            WHERE course_id = $1
        """, course_id, metadata, assessment, usage)
    finally:
        await conn.close()

async def find_job_by_prefix(prefix: str) -> Optional[Dict[str, Any]]:
    """
    Finds a completed job that matches the prefix (Base Hash).
    Used to find a template to clone from.
    """
    conn: asyncpg.Connection = await get_conn()
    try:
        # Optimization: Prefer jobs that haven't been edited if we track that?
        # For now, just grab the most recently updated successful one
        row = await conn.fetchrow("""
            SELECT * FROM interactive_assessments 
            WHERE course_id LIKE $1 || '%' 
            AND status = 'COMPLETED' 
            ORDER BY updated_at DESC 
            LIMIT 1
        """, prefix)
        return dict(row) if row else None
    finally:
        await conn.close()

async def create_completed_job(course_id: str, user_id: str, metadata: dict, assessment: dict, usage: dict):
    """
    Creates a new job record directly in COMPLETED state (Cloning).
    """
    conn: asyncpg.Connection = await get_conn()
    try:
        await conn.execute("""
            INSERT INTO interactive_assessments 
            (course_id, user_id, status, metadata, assessment_data, token_usage, updated_at)
            VALUES ($1, $2, 'COMPLETED', $3, $4, $5, NOW())
            ON CONFLICT (course_id) DO NOTHING
        """, course_id, user_id, metadata, assessment, usage)
    finally:
        await conn.close()

async def update_job_result(job_id: str, user_id: str, new_assessment_data: dict) -> bool:
    """
    Updates the assessment result (Edit Mode).
    Enforces ownership (user_id must match).
    """
    conn: asyncpg.Connection = await get_conn()
    try:
        result = await conn.execute("""
            UPDATE interactive_assessments
            SET assessment_data = $3, updated_at = NOW()
            WHERE course_id = $1 AND user_id = $2
        """, job_id, user_id, new_assessment_data)
        
        # Check if rows were updated (if 0, implies job not found or unauthorized)
        # "UPDATE 1" -> 'UPDATE' in tag, '1' in rows
        success = result != "UPDATE 0"
    finally:
        await conn.close()
    return success

async def get_user_assessments_history(user_id: str) -> List[Dict[str, Any]]:
    """
    Fetches the assessment history for a specific user.
    """
    conn: asyncpg.Connection = await get_conn()
    try:
        rows = await conn.fetch("""
            SELECT course_id as job_id, status, created_at, updated_at, metadata, error_message
            FROM interactive_assessments
            WHERE user_id = $1
            ORDER BY updated_at DESC
        """, user_id)
        data = [dict(row) for row in rows]
    finally:
        await conn.close()
    return data
