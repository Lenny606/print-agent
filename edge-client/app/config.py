import os
import platform
import sqlite3
from pathlib import Path
import logging

logger = logging.getLogger("PrintAgent.Config")

# Environment settings
CLOUD_API_URL = os.getenv("CLOUD_API_URL", "http://localhost:5000")
INSTALL_TOKEN = os.getenv("INSTALL_TOKEN", "")
AGENT_NAME = os.getenv("AGENT_NAME", "PythonEdgeAgent")

# SQLite DB Path
DB_PATH = Path(os.getenv("EDGE_DB_PATH", "edge_queue.db")).resolve()

# Keyring configuration
KEYRING_SERVICE_NAME = "HybridEdgePrintAgent"
KEYRING_CLIENT_ID_KEY = "client_id"
KEYRING_CLIENT_SECRET_KEY = "client_secret"

def is_windows() -> bool:
    # If explicitly running in Docker, treat as Linux even if host is Windows
    if os.getenv("DOCKER_ENV", "").lower() in ("true", "1"):
        return False
    return platform.system() == "Windows"

def _init_sqlite_fallback_db():
    """Ensure the credentials table exists in SQLite if fallback is used."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_credentials (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def _save_to_sqlite(client_id: str, client_secret: str):
    _init_sqlite_fallback_db()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO agent_credentials (key, value) VALUES (?, ?)",
            (KEYRING_CLIENT_ID_KEY, client_id)
        )
        cursor.execute(
            "INSERT OR REPLACE INTO agent_credentials (key, value) VALUES (?, ?)",
            (KEYRING_CLIENT_SECRET_KEY, client_secret)
        )
        conn.commit()
    finally:
        conn.close()

def _load_from_sqlite() -> tuple[str | None, str | None]:
    _init_sqlite_fallback_db()
    conn = sqlite3.connect(str(DB_PATH))
    client_id = None
    client_secret = None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM agent_credentials WHERE key = ?",
            (KEYRING_CLIENT_ID_KEY,)
        )
        row = cursor.fetchone()
        if row:
            client_id = row[0]

        cursor.execute(
            "SELECT value FROM agent_credentials WHERE key = ?",
            (KEYRING_CLIENT_SECRET_KEY,)
        )
        row = cursor.fetchone()
        if row:
            client_secret = row[0]
    finally:
        conn.close()
    return client_id, client_secret

def save_credentials(client_id: str, client_secret: str) -> bool:
    """
    Saves the agent's client ID and client secret.
    Uses Keyring on native Windows, and SQLite fallback on Linux/Docker.
    """
    if is_windows():
        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_CLIENT_ID_KEY, client_id)
            keyring.set_password(KEYRING_SERVICE_NAME, KEYRING_CLIENT_SECRET_KEY, client_secret)
            logger.info("Successfully saved credentials in Windows Credential Manager.")
            return True
        except Exception as e:
            logger.warning(f"Keyring failed to save credentials: {e}. Falling back to SQLite.")

    # Fallback/Linux/Docker
    try:
        _save_to_sqlite(client_id, client_secret)
        logger.info("Successfully saved credentials in local SQLite storage.")
        return True
    except Exception as e:
        logger.error(f"Failed to save credentials to SQLite fallback: {e}")
        return False

def load_credentials() -> tuple[str | None, str | None]:
    """
    Loads the client ID and client secret.
    Tries Keyring first on Windows, then falls back to SQLite database storage.
    """
    if is_windows():
        try:
            import keyring
            client_id = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_CLIENT_ID_KEY)
            client_secret = keyring.get_password(KEYRING_SERVICE_NAME, KEYRING_CLIENT_SECRET_KEY)
            if client_id and client_secret:
                logger.info("Credentials loaded from Windows Credential Manager.")
                return client_id, client_secret
        except Exception as e:
            logger.warning(f"Keyring failed to load credentials: {e}. Checking SQLite fallback.")

    # Fallback/Linux/Docker
    try:
        client_id, client_secret = _load_from_sqlite()
        if client_id and client_secret:
            logger.info("Credentials loaded from SQLite local DB.")
        return client_id, client_secret
    except Exception as e:
        logger.error(f"Failed to load credentials from SQLite fallback: {e}")
        return None, None
