import asyncio
import logging
import sys
import tempfile
from pathlib import Path
from datetime import datetime

from app.config import CLOUD_API_URL, INSTALL_TOKEN, DB_PATH
from app.database import init_db, enqueue_job, get_next_queued_job, update_job_status
from app.onboarding import register_agent
from app.network import CloudClient
from app.printer import send_zpl_to_printer, print_pdf_file, discover_local_printers

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("PrintAgent.Main")

class EdgeAgent:
    def __init__(self):
        self.client = CloudClient()
        self.printers = {} # printer_id -> printer config dict
        self.printers_lock = asyncio.Lock()
        self.shutdown_event = asyncio.Event()

    async def initialize(self):
        """Prepares database and performs onboarding if needed."""
        # 1. Init Database
        await init_db()

        # 2. Onboard/Authenticate check
        if not self.client.is_authenticated():
            logger.info("Credentials not found. Starting onboarding workflow...")
            onboarded = await register_agent()
            if not onboarded:
                logger.error("Onboarding failed. Will retry registration in background...")
            else:
                self.client.refresh_credentials()

    async def run(self):
        """Starts all concurrent agent tasks."""
        await self.initialize()

        # Run loops
        tasks = [
            asyncio.create_task(self.onboarding_loop(), name="OnboardingLoop"),
            asyncio.create_task(self.config_sync_loop(), name="ConfigSyncLoop"),
            asyncio.create_task(self.job_polling_loop(), name="JobPollingLoop"),
            asyncio.create_task(self.job_processing_loop(), name="JobProcessingLoop"),
            asyncio.create_task(self.discovery_loop(), name="DiscoveryLoop")
        ]

        logger.info("All background tasks spawned successfully.")
        
        try:
            # Wait for shutdown event or tasks to throw exception
            await self.shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Shutdown requested. Cancelling background tasks...")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("Agent stopped.")

    async def onboarding_loop(self):
        """Retries onboarding if agent is not registered."""
        backoff = 2.0
        while not self.shutdown_event.is_set():
            if self.client.is_authenticated():
                # Already authenticated, sleep and check again later (idle)
                await asyncio.sleep(10.0)
                continue

            logger.warning("Agent is not authenticated. Retrying onboarding...")
            success = await register_agent()
            if success:
                self.client.refresh_credentials()
                backoff = 2.0
            else:
                logger.warning(f"Onboarding failed. Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def config_sync_loop(self):
        """Periodically syncs agent printer configurations from cloud."""
        while not self.shutdown_event.is_set():
            if not self.client.is_authenticated():
                await asyncio.sleep(5.0)
                continue

            logger.info("Syncing agent configurations from Central Cloud...")
            config_data = await self.client.get_config()
            if config_data and "printers" in config_data:
                async with self.printers_lock:
                    self.printers = {p["id"]: p for p in config_data["printers"]}
                logger.info(f"Configuration synced. Registered printers: {list(self.printers.keys())}")
                # Sync successfully, sleep 60 seconds
                await asyncio.sleep(60.0)
            else:
                logger.warning("Failed to retrieve configuration. Retrying in 10s...")
                await asyncio.sleep(10.0)

    async def job_polling_loop(self):
        """Polls cloud for new print jobs and enqueues them locally."""
        while not self.shutdown_event.is_set():
            if not self.client.is_authenticated():
                await asyncio.sleep(5.0)
                continue

            # Poll jobs
            jobs = await self.client.poll_jobs()
            if jobs is not None:
                if len(jobs) > 0:
                    logger.info(f"Polled {len(jobs)} print jobs from central cloud.")
                for job in jobs:
                    # Enqueue locally
                    await enqueue_job(
                        job_id=job["id"],
                        printer_id=job["printer_id"],
                        file_url=job.get("file_url"),
                        zpl_data=job.get("zpl_data"),
                        print_type=job["print_type"]
                    )
                # Success polling, poll again in 3 seconds
                await asyncio.sleep(3.0)
            else:
                logger.warning("Failed to poll jobs. Retrying in 10s...")
                await asyncio.sleep(10.0)

    async def job_processing_loop(self):
        """Processes enqueued local print jobs and reports execution status."""
        while not self.shutdown_event.is_set():
            job = await get_next_queued_job()
            if not job:
                # No jobs, sleep a bit
                await asyncio.sleep(1.0)
                continue

            job_id = job["id"]
            printer_id = job["printer_id"]
            print_type = job["print_type"]
            file_url = job.get("file_url")
            zpl_data = job.get("zpl_data")

            logger.info(f"Processing job {job_id} (Type: {print_type}) for printer {printer_id}...")

            # 1. Find printer configuration
            async with self.printers_lock:
                printer_config = self.printers.get(printer_id)

            if not printer_config:
                error_msg = f"Printer ID {printer_id} not configured locally on this agent."
                logger.error(error_msg)
                await update_job_status(job_id, "FAILED", error_msg)
                await self.client.report_job_status(job_id, "FAILED", error_msg)
                continue

            ip_address = printer_config["ip_address"]
            port = printer_config["port"]
            print_type_mapped = printer_config["print_type"]

            # 2. Execute printing
            success = False
            error_msg = None

            try:
                if print_type_mapped.upper() == "ZPL":
                    if zpl_data:
                        success = await send_zpl_to_printer(ip_address, port, zpl_data)
                        if not success:
                            error_msg = "ZPL connection or transmission failed."
                    else:
                        error_msg = "Job print type is ZPL but no ZPL data payload was provided."
                        logger.error(error_msg)

                elif print_type_mapped.upper() == "PDF":
                    if file_url:
                        # Create a temp file path to download to
                        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                            tmp_path = Path(tmp.name)
                        
                        try:
                            # Stream download
                            downloaded = await self.client.download_file_from_s3(file_url, tmp_path)
                            if downloaded:
                                # Execute system print (we map printer name as printer_id or name)
                                success = await print_pdf_file(tmp_path, printer_config["name"])
                                if not success:
                                    error_msg = "CUPS system print execution failed."
                            else:
                                error_msg = "Failed to download print asset from S3."
                        finally:
                            if tmp_path.exists():
                                tmp_path.unlink()
                    else:
                        error_msg = "Job print type is PDF but no file_url asset was provided."
                        logger.error(error_msg)
                else:
                    error_msg = f"Unsupported print type config mapping: {print_type_mapped}"
                    logger.error(error_msg)

            except Exception as e:
                error_msg = f"Unexpected error during job execution: {e}"
                logger.exception(error_msg)

            # 3. Save status locally and report to central cloud API
            if success:
                await update_job_status(job_id, "PRINTED")
                await self.client.report_job_status(job_id, "PRINTED")
            else:
                await update_job_status(job_id, "FAILED", error_msg)
                await self.client.report_job_status(job_id, "FAILED", error_msg)

    async def discovery_loop(self):
        """Periodically scans the subnet and reports discovered devices to cloud."""
        while not self.shutdown_event.is_set():
            if not self.client.is_authenticated():
                await asyncio.sleep(5.0)
                continue

            logger.info("Initiating dynamic printer auto-discovery subnet scan...")
            try:
                # Scan port 9100 listeners
                devices = await discover_local_printers()
                await self.client.report_discovered_devices(devices)
            except Exception as e:
                logger.error(f"Error during network scanner loop: {e}")

            # Run discovery every 5 minutes (300 seconds)
            await asyncio.sleep(300.0)

def main():
    agent = EdgeAgent()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(agent.run())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received. Stopping agent...")
        agent.shutdown_event.set()
        loop.run_until_complete(asyncio.sleep(0.5))
    finally:
        loop.close()

if __name__ == "__main__":
    main()
