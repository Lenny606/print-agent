import sys
import os
from pathlib import Path

# Add parent directory of 'app' to python path to ensure imports work
# when run as a service directly or frozen by PyInstaller.
if getattr(sys, 'frozen', False):
    app_dir = Path(sys.executable).parent
else:
    app_dir = Path(__file__).resolve().parent

if str(app_dir) not in sys.path:
    sys.path.insert(0, str(app_dir))

from app.config import get_data_dir

# Redirect stdout/stderr to a file in the data dir
data_dir = get_data_dir()
log_dir = data_dir / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "service.log"

class FileRedirector:
    def __init__(self, filepath):
        self.file = open(filepath, "a", encoding="utf-8")
    def write(self, data):
        self.file.write(data)
        self.file.flush()
    def flush(self):
        self.file.flush()

# Redirect early before any imports that configure logging
sys.stdout = FileRedirector(log_file)
sys.stderr = sys.stdout

import win32serviceutil
import win32service
import win32event
import servicemanager
import asyncio
import logging

logger = logging.getLogger("PrintAgent.Service")

# Now import the EdgeAgent
from app.main import EdgeAgent

class PrintAgentService(win32serviceutil.ServiceFramework):
    _svc_name_ = "HybridEdgePrintAgent"
    _svc_display_name_ = "Hybrid Edge Print Agent"
    _svc_description_ = "Polls print jobs from central cloud and prints to local printers"

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.agent = None
        self.loop = None

    def SvcStop(self):
        logger.info("Service stop request received from Windows Service Control Manager.")
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self.agent and self.loop:
            logger.info("Signalling EdgeAgent shutdown event thread-safely.")
            self.loop.call_soon_threadsafe(self.agent.shutdown_event.set)
        else:
            logger.warning("Service stopped before Agent or Event Loop was fully initialized.")

    def SvcDoRun(self):
        # Change working directory to the directory of the executable to avoid using System32
        if getattr(sys, 'frozen', False):
            exe_dir = Path(sys.executable).parent
        else:
            exe_dir = Path(__file__).resolve().parent
        os.chdir(str(exe_dir))
        
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, '')
        )
        logger.info("Windows Service thread running SvcDoRun.")
        self.main()

    def main(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.agent = EdgeAgent()
        
        try:
            logger.info("Starting EdgeAgent run loop...")
            self.loop.run_until_complete(self.agent.run())
            logger.info("EdgeAgent run loop completed successfully.")
        except Exception as e:
            logger.exception(f"PrintAgentService ran into an uncaught exception: {e}")
            servicemanager.LogErrorMsg(f"PrintAgentService error: {str(e)}")
        finally:
            self.loop.close()
            logger.info("Event loop closed. Service thread exiting.")

if __name__ == '__main__':
    # If run without arguments, we are starting as a service
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(PrintAgentService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(PrintAgentService)
