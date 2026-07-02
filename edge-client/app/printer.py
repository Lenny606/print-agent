import socket
import subprocess
import asyncio
import logging
from pathlib import Path

logger = logging.getLogger("PrintAgent.Printer")

async def send_zpl_to_printer(ip_address: str, port: int, zpl_data: str) -> bool:
    """
    Sends raw ZPL data to a thermal label printer using a TCP socket.
    """
    logger.info(f"Sending ZPL job to printer at {ip_address}:{port}...")
    try:
        # Wrap blocking socket code in asyncio executor
        loop = asyncio.get_running_loop()
        def _send():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(10.0)
                s.connect((ip_address, port))
                s.sendall(zpl_data.encode('utf-8'))
                s.shutdown(socket.SHUT_WR)
        
        await loop.run_in_executor(None, _send)
        logger.info(f"Successfully transmitted ZPL payload to {ip_address}:{port}")
        return True
    except Exception as e:
        logger.error(f"Failed to transmit ZPL to printer {ip_address}:{port} : {e}")
        return False

async def print_pdf_file(file_path: Path, printer_name_or_queue: str) -> bool:
    """
    Prints a local PDF file using Linux CUPS commands (lp).
    """
    logger.info(f"Printing PDF {file_path} on queue '{printer_name_or_queue}'...")
    if not file_path.exists():
        logger.error(f"PDF file does not exist: {file_path}")
        return False

    try:
        # Construct the lp command
        cmd = ["lp", "-d", printer_name_or_queue, str(file_path)]
        logger.debug(f"Executing system command: {' '.join(cmd)}")
        
        # Execute asynchronously in a thread pool to avoid blocking the main event loop
        loop = asyncio.get_running_loop()
        def _run_lp():
            return subprocess.run(cmd, capture_output=True, text=True, check=True)
            
        result = await loop.run_in_executor(None, _run_lp)
        logger.info(f"Successfully sent PDF to print queue: {result.stdout.strip()}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"System lp print command failed. Code: {e.returncode}, Error: {e.stderr.strip()}")
        return False
    except Exception as e:
        logger.error(f"Failed to execute print command: {e}")
        return False

async def _check_host(ip: str, port: int, timeout: float = 0.5) -> dict | None:
    """Helper to check if a specific IP host has port open."""
    try:
        conn = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        logger.info(f"Discovered active printer at {ip}:{port}")
        return {"ip_address": ip, "port": port}
    except Exception:
        return None

def _get_local_ip() -> str:
    """Retrieves the local IP address used for network access."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable or send any packet
        s.connect(('8.8.8.8', 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
    return local_ip

async def discover_local_printers(subnet_prefix: str | None = None) -> list[dict]:
    """
    Scans the local subnet `/24` for hosts listening on port 9100.
    Scans concurrently using asyncio for rapid discovery.
    """
    if not subnet_prefix:
        local_ip = _get_local_ip()
        if local_ip == '127.0.0.1':
            logger.warning("Local loopback detected. Subnet scanning skipped.")
            return []
        
        parts = local_ip.split('.')
        subnet_prefix = f"{parts[0]}.{parts[1]}.{parts[2]}"

    logger.info(f"Starting auto-discovery scanner on subnet: {subnet_prefix}.0/24")
    
    tasks = []
    for host in range(1, 255):
        ip = f"{subnet_prefix}.{host}"
        tasks.append(_check_host(ip, 9100, timeout=0.5))

    results = await asyncio.gather(*tasks)
    discovered = [r for r in results if r is not None]
    
    logger.info(f"Discovery scanner complete. Found {len(discovered)} printers.")
    return discovered
