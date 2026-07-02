import subprocess
import time
import httpx
import socket
import threading
import sys
import os
from pathlib import Path

# Configuration
API_URL = "http://localhost:5000"
TEST_TOKEN = "TEST-INSTALL-TOKEN-123"
MOCK_PRINTER_PORT = 9999

# Test databases to clean up
DB_FILES = ["cloud_print.db", "edge-client/test_edge_queue.db"]


# Global lists for cleanup
processes = []
received_zpl_data = []
zpl_server_running = True

def run_zpl_mock_server():
    """Mocks a physical ZPL printer listening on port 9999."""
    global received_zpl_data, zpl_server_running
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', MOCK_PRINTER_PORT))
    s.listen(1)
    s.settimeout(1.0)
    
    print(f"[Mock Printer] Server listening on port {MOCK_PRINTER_PORT}...", flush=True)
    
    while zpl_server_running:
        try:
            conn, addr = s.accept()
            print(f"[Mock Printer] Accepted connection from {addr}", flush=True)
            data_buffer = []
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                data_buffer.append(data.decode('utf-8'))
            payload = "".join(data_buffer)
            print(f"[Mock Printer] Received payload:\n{payload}", flush=True)
            received_zpl_data.append(payload)
            conn.close()
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[Mock Printer] Server error: {e}", flush=True)
            break
    s.close()
    print("[Mock Printer] Server stopped.", flush=True)

def cleanup():
    """Kills running subprocesses and deletes temp database files."""
    global zpl_server_running
    zpl_server_running = False
    
    print("\n--- Cleaning up processes ---", flush=True)
    for p in processes:
        try:
            p.terminate()
            p.wait(timeout=2)
            print(f"Terminated process {p.pid}", flush=True)
        except Exception as e:
            print(f"Error terminating process: {e}", flush=True)
            try:
                p.kill()
            except Exception:
                pass

    print("\n--- Cleaning up database files ---", flush=True)
    for db in DB_FILES:
        p = Path(db)
        if p.exists():
            try:
                p.unlink()
                print(f"Deleted database file: {db}", flush=True)
            except Exception as e:
                print(f"Failed to delete {db}: {e}", flush=True)

def main():
    # Clean up any leftover databases before starting
    for db in DB_FILES:
        p = Path(db)
        if p.exists():
            p.unlink()

    # 1. Start Mock ZPL Printer Server
    printer_thread = threading.Thread(target=run_zpl_mock_server, daemon=True)
    printer_thread.start()

    try:
        # 2. Start Cloud API
        print("\n--- Starting Central Cloud API (.NET 10) ---", flush=True)
        # Force URLs to http://localhost:5000 and ensure connection string targets our test file
        api_env = os.environ.copy()
        api_env["ConnectionStrings__DefaultConnection"] = "Data Source=cloud_print.db"
        api_env["InstallToken"] = TEST_TOKEN
        
        api_proc = subprocess.Popen(
            ["dotnet", "run", "--project", "cloud-api/CloudApi/CloudApi.csproj", "--urls", "http://localhost:5000"],
            env=api_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        processes.append(api_proc)

        # Wait for API to be ready
        print("Waiting for Cloud API to initialize...", flush=True)
        api_ready = False
        for _ in range(20):
            try:
                response = httpx.get(f"{API_URL}/v1/debug/status", timeout=1.0)
                if response.status_code == 200:
                    api_ready = True
                    print("Cloud API is ready and running.", flush=True)
                    break
            except Exception:
                pass
            time.sleep(1.0)

        if not api_ready:
            print("ERROR: Cloud API failed to start. Output:")
            out, _ = api_proc.communicate()
            print(out)
            sys.exit(1)

        # 3. Start Python Edge Agent
        print("\n--- Starting Python Edge Agent ---", flush=True)
        agent_env = os.environ.copy()
        agent_env["CLOUD_API_URL"] = API_URL
        agent_env["INSTALL_TOKEN"] = TEST_TOKEN
        agent_env["EDGE_DB_PATH"] = "test_edge_queue.db"
        agent_env["AGENT_NAME"] = "IntegrationTestAgent"
        agent_env["DOCKER_ENV"] = "true"  # Force SQLite credentials storage fallback
        agent_env["PYTHONPATH"] = str(Path(__file__).parent / "edge-client")

        agent_proc = subprocess.Popen(
            [sys.executable, "-m", "app.main"],
            cwd=str(Path(__file__).parent / "edge-client"),
            env=agent_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        processes.append(agent_proc)

        # Wait for Agent to perform registration handshake and appear in API
        print("Waiting for agent registration handshake...", flush=True)
        agent_id = None
        for _ in range(15):
            try:
                response = httpx.get(f"{API_URL}/v1/debug/status")
                data = response.json()
                agents = data.get("agents", [])
                if agents:
                    agent_id = agents[0]["id"]
                    print(f"Agent successfully registered. ID: {agent_id}", flush=True)
                    break
            except Exception as e:
                print(f"Error checking registration: {e}", flush=True)
            time.sleep(1.0)

        if not agent_id:
            print("ERROR: Agent failed to register. Agent output snapshot:")
            # Read whatever output is available
            # To prevent blocking, we use non-blocking check
            time.sleep(2.0)
            sys.exit(1)

        # 4. Map Printer in Cloud API
        print("\n--- Registering printer config map in Cloud API ---", flush=True)
        printer_payload = {
            "id": "printer-zpl-1",
            "agent_id": agent_id,
            "name": "TestZplPrinter",
            "ip_address": "127.0.0.1",
            "port": MOCK_PRINTER_PORT,
            "print_type": "ZPL"
        }
        res = httpx.post(f"{API_URL}/v1/printers", json=printer_payload)
        assert res.status_code == 200, f"Failed to map printer: {res.text}"
        print("Printer mapped successfully.", flush=True)

        # Give the agent a few seconds to sync the configuration
        print("Waiting for agent config sync...", flush=True)
        time.sleep(5.0)

        # 5. Submit ZPL Print Job Upstream
        print("\n--- Submitting ZPL Job upstream ---", flush=True)
        test_zpl = "^XA^FO50,50^A0N,50,50^FDHello Integration Test^FS^XZ"
        job_payload = {
            "agent_id": agent_id,
            "printer_id": "printer-zpl-1",
            "zpl_data": test_zpl,
            "print_type": "ZPL",
            "ttl_seconds": 30
        }
        res = httpx.post(f"{API_URL}/v1/print", json=job_payload)
        assert res.status_code == 200, f"Failed to submit print job: {res.text}"
        job_id = res.json()["jobId"]
        print(f"Print job submitted. Job ID: {job_id}", flush=True)

        # 6. Verify Job processing and status reports
        print("Waiting for job processing and transmission...", flush=True)
        job_printed = False
        for _ in range(15):
            # Check if Mock Printer received the payload
            if received_zpl_data:
                assert received_zpl_data[0] == test_zpl, "ZPL payload did not match!"
                print("Mock ZPL Printer successfully received the matching ZPL payload!", flush=True)
                job_printed = True
                break
            time.sleep(1.0)

        assert job_printed, "Failed: Mock ZPL printer never received the payload."

        # Verify job status updated to PRINTED in Cloud API
        print("Checking job status on Cloud API...", flush=True)
        status_ok = False
        for _ in range(5):
            res = httpx.get(f"{API_URL}/v1/debug/status")
            jobs = res.json().get("jobs", [])
            test_job = next((j for j in jobs if j["id"] == job_id), None)
            if test_job and test_job["status"] == "PRINTED":
                print("Cloud API reports job status: PRINTED. Success!", flush=True)
                status_ok = True
                break
            time.sleep(1.0)

        assert status_ok, "Failed: Cloud API did not reflect job status as PRINTED."

        print("\n=========================================", flush=True)
        print("  INTEGRATION TEST PASSED SUCCESSFULLY!  ", flush=True)
        print("=========================================", flush=True)

    except Exception as e:
        print(f"\nIntegration test failed: {e}", flush=True)
        # Print logs of processes to debug
        print("\n--- Cloud API Process Logs ---", flush=True)
        # We can read some lines from stdout since it is a PIPE
        # Set to non-blocking to prevent hang
        os.set_blocking(api_proc.stdout.fileno(), False)
        print(api_proc.stdout.read() or "No logs available.")
        
        print("\n--- Agent Process Logs ---", flush=True)
        os.set_blocking(agent_proc.stdout.fileno(), False)
        print(agent_proc.stdout.read() or "No logs available.")
        
        sys.exit(1)
    finally:
        cleanup()

if __name__ == "__main__":
    main()
