# Windows Edge Print Agent - Installation & User Manual

This manual provides detailed instructions on how to build, package, install, configure, and manage the Hybrid Edge Print Agent on Microsoft Windows systems.

---

## 1. Prerequisites

Before building the print agent or compilation of the installer, ensure the following software is installed on the build machine:

### A. Python Environment
- **Python 3.10+ (64-bit)**. Enable the option to **"Add Python to PATH"** during installation.
- Verify Python and `pip` installation by running in Command Prompt:
  ```cmd
  python --version
  pip --version
  ```

### B. Inno Setup Compiler
- **Inno Setup 6+** is required to compile the wizard-driven installer.
- Download and install it from the [Inno Setup Downloads page](https://jrsoftware.org/isdl.php).

### C. PDF Printing Engine (SumatraPDF)
- The agent utilizes **SumatraPDF** for command-line PDF printing under Windows.
- Download the 64-bit command-line/portable executable version from the [SumatraPDF website](https://www.sumatrapdfreader.org/free-pdf-reader).
- Save the downloaded executable as `SumatraPDF.exe` and place it in the `edge-client/` directory.

---

## 2. Compilation and Packaging

Follow these steps to compile the Python agent into a standalone executable:

1. Open **Command Prompt** (cmd) or **PowerShell** as Administrator.
2. Navigate to the `edge-client/` directory:
   ```cmd
   cd edge-client
   ```
3. Run the packaging batch script:
   ```cmd
   build_windows.bat
   ```

### What `build_windows.bat` Does:
1. Installs/updates dependencies (`pip install -r requirements.txt`).
2. Installs build tools (`pyinstaller`, `pywin32`).
3. Compiles the service wrapper `windows_service.py` (which embeds the `app/` folder logic) into a single, standalone binary located at `edge-client/dist/edge_print_agent.exe`.

---

## 3. Creating the Installer Package

Once `dist/edge_print_agent.exe` is successfully built:

1. Verify that `SumatraPDF.exe` is present in the `edge-client/` folder.
2. Open **Inno Setup Compiler**.
3. Load the script file `edge-client/installer.iss`.
4. Click **Build > Compile** (or press `Ctrl + F9`).
5. Upon completion, the setup executable `PrintAgentSetup.exe` will be generated in `edge-client/Output/`.

---

## 4. Installing the Agent

Run the generated `PrintAgentSetup.exe` on target client machines. The installation wizard will guide you through the setup:

### Wizard Inputs:
1. **Installation Path:** Defaults to `C:\Program Files\PrintAgent`.
2. **Cloud API URL:** Enter the central API endpoint (e.g., `https://your-central-cloud-url.com` or `http://localhost:5000` for local development).
3. **Installation Token:** Enter the security onboarding token provided by the central cloud to register new agents.
4. **Agent Name:** Name used to identify this agent instance in the cloud interface.
5. **Credential Storage Option:**
   - **SQLite Database (Recommended):** Safely stores the onboarding `client_id` and `client_secret` in a local SQLite database (`C:\ProgramData\PrintAgent\edge_queue.db`). *Recommended since Windows Services run as background SYSTEM accounts and may lack user-specific Credential Manager profiles.*
   - **Keyring (Windows Credential Manager):** Uses native OS credential vault storage.

Upon completion, the installer automatically writes a `config.json` containing the wizard inputs into the installation directory, registers the executable as a Windows Service, and starts it.

---

## 5. Service Configuration & Storage Directories

After installation, the agent operates in the background:

- **Installation Directory:** `C:\Program Files\PrintAgent\`
  - Contains `edge_print_agent.exe`, `SumatraPDF.exe`, and the local `config.json`.
- **Common Application Data Directory:** `C:\ProgramData\PrintAgent\`
  - Contains the SQLite print queue/credentials database `edge_queue.db`.
- **Logs Directory:** `C:\ProgramData\PrintAgent\logs\`
  - Contains `service.log` detailing service start/stop SCM events, polling logs, and execution errors.

---

## 6. Manual Command-Line Service Control

You can manually control the service using Command Prompt (must run as Administrator) by calling the executable directly with service commands:

| Command | Action | Description |
|---|---|---|
| `edge_print_agent.exe install` | Register Service | Registers the agent in Windows Service Control Manager (SCM). |
| `edge_print_agent.exe start` | Start Service | Starts the registered service in the background. |
| `edge_print_agent.exe stop` | Stop Service | Stops the running service safely. |
| `edge_print_agent.exe remove` | Delete Service | Removes the service registration from Windows. |

Alternatively, you can manage it using standard Windows tools like the **Services MMC console** (`services.msc`) targeting the service named **"Hybrid Edge Print Agent"** (`HybridEdgePrintAgent`).

---

## 7. Troubleshooting and Diagnostic Logs

If print jobs are not processed or the agent fails to start:
1. Check the logs at `C:\ProgramData\PrintAgent\logs\service.log`.
2. Ensure the central Cloud API is reachable from the target machine by querying the status endpoint:
   ```cmd
   curl http://localhost:5000/v1/debug/status
   ```
3. Verify that target printers are configured correctly under the mapped Agent ID in the Cloud API.
