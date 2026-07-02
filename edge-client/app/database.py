import aiosqlite
import logging
from datetime import datetime
from app.config import DB_PATH

logger = logging.getLogger("PrintAgent.Database")

async def init_db():
    """Initializes the SQLite database tables."""
    logger.info(f"Initializing SQLite database at: {DB_PATH}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS local_jobs (
                id TEXT PRIMARY KEY,
                printer_id TEXT NOT NULL,
                file_url TEXT,
                zpl_data TEXT,
                print_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error_message TEXT,
                retries INTEGER DEFAULT 0
            )
            """
        )
        await db.commit()

async def enqueue_job(job_id: str, printer_id: str, file_url: str | None, zpl_data: str | None, print_type: str) -> bool:
    """Enqueues a print job in the local SQLite database if not already present."""
    now_str = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute(
                """
                INSERT INTO local_jobs (id, printer_id, file_url, zpl_data, print_type, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?)
                """,
                (job_id, printer_id, file_url, zpl_data, print_type, now_str, now_str)
            )
            await db.commit()
            logger.info(f"Enqueued job {job_id} in local database.")
            return True
        except aiosqlite.IntegrityError:
            logger.info(f"Job {job_id} already exists in local database. Skipping insert.")
            return False

async def get_next_queued_job() -> dict | None:
    """Retrieves the oldest queued or retriable failed job (limit 3 retries)."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM local_jobs 
            WHERE status = 'QUEUED' OR (status = 'FAILED' AND retries < 3)
            ORDER BY created_at ASC LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

async def update_job_status(job_id: str, status: str, error_message: str | None = None):
    """Updates the status and increments the retry count if failed."""
    now_str = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if status == "FAILED":
            await db.execute(
                """
                UPDATE local_jobs 
                SET status = ?, error_message = ?, retries = retries + 1, updated_at = ?
                WHERE id = ?
                """,
                (status, error_message, now_str, job_id)
            )
        else:
            await db.execute(
                """
                UPDATE local_jobs 
                SET status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error_message, now_str, job_id)
            )
        await db.commit()
        logger.info(f"Updated job {job_id} status to {status}.")
