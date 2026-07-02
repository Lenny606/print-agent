import httpx
import logging
from pathlib import Path
from app.config import CLOUD_API_URL, load_credentials

logger = logging.getLogger("PrintAgent.Network")

class CloudClient:
    def __init__(self):
        self.client_id = None
        self.client_secret = None
        self.headers = {}
        self.refresh_credentials()

    def refresh_credentials(self) -> bool:
        """Loads credentials and updates the auth headers."""
        self.client_id, self.client_secret = load_credentials()
        if self.client_id and self.client_secret:
            self.headers = {
                "X-Agent-ID": self.client_id,
                "X-Agent-Secret": self.client_secret,
                "Content-Type": "application/json"
            }
            return True
        else:
            self.headers = {}
            return False

    def is_authenticated(self) -> bool:
        return bool(self.client_id and self.client_secret)

    async def get_config(self) -> dict | None:
        """Fetches the latest printer configuration mapping from the cloud."""
        if not self.is_authenticated() and not self.refresh_credentials():
            logger.error("Cannot fetch config: Client is not authenticated.")
            return None

        url = f"{CLOUD_API_URL}/v1/edge/config"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, timeout=10.0)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Failed to fetch config. HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error fetching configuration: {e}")
        return None

    async def poll_jobs(self) -> list | None:
        """Polls the cloud API for any pending print jobs."""
        if not self.is_authenticated() and not self.refresh_credentials():
            logger.error("Cannot poll jobs: Client is not authenticated.")
            return None

        url = f"{CLOUD_API_URL}/v1/edge/jobs/poll"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=self.headers, timeout=10.0)
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Failed to poll jobs. HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error polling jobs: {e}")
        return None

    async def report_job_status(self, job_id: str, status: str, error_message: str | None = None) -> bool:
        """Reports print job status back to the central Cloud API."""
        if not self.is_authenticated() and not self.refresh_credentials():
            logger.error("Cannot report status: Client is not authenticated.")
            return False

        url = f"{CLOUD_API_URL}/v1/edge/jobs/{job_id}/status"
        payload = {
            "status": status,
            "error_message": error_message
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, headers=self.headers, timeout=10.0)
                if response.status_code == 200:
                    logger.info(f"Reported status {status} for job {job_id} to cloud.")
                    return True
                else:
                    logger.error(f"Failed to report job status. HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error reporting job status: {e}")
        return False

    async def report_discovered_devices(self, devices: list[dict]) -> bool:
        """Sends lists of discovered network printer IP addresses to the cloud."""
        if not self.is_authenticated() and not self.refresh_credentials():
            logger.error("Cannot report devices: Client is not authenticated.")
            return False

        url = f"{CLOUD_API_URL}/v1/edge/discovered-devices"
        # devices list element structure: {"ip_address": str, "port": int}
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=devices, headers=self.headers, timeout=10.0)
                if response.status_code == 200:
                    logger.info("Successfully reported discovered devices to cloud.")
                    return True
                else:
                    logger.error(f"Failed to report discovered devices. HTTP {response.status_code}: {response.text}")
        except Exception as e:
            logger.error(f"Error reporting discovered devices: {e}")
        return False

    async def download_file_from_s3(self, url: str, dest_path: Path) -> bool:
        """
        Downloads a print job file from S3 / web URL using stream chunk writing
        to avoid loading large files into memory.
        """
        logger.info(f"Downloading printing asset from {url} to {dest_path}...")
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient() as client:
                async with client.stream("GET", url, timeout=30.0) as response:
                    if response.status_code != 200:
                        logger.error(f"Failed to download asset. HTTP code: {response.status_code}")
                        return False
                    
                    with open(dest_path, "wb") as f:
                        async for chunk in response.iter_bytes(chunk_size=8192):
                            if chunk:
                                f.write(chunk)
            
            logger.info(f"Asset downloaded successfully. File size: {dest_path.stat().st_size} bytes.")
            return True
        except Exception as e:
            logger.error(f"Error downloading asset from S3: {e}")
            if dest_path.exists():
                try:
                    dest_path.unlink()
                except Exception:
                    pass
            return False
