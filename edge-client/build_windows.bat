@echo off
echo ===================================================
echo Building Edge Print Agent Windows Executable
echo ===================================================

:: Ensure python is in path
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: Python is not installed or not in PATH.
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller pywin32

echo Running PyInstaller...
python -m PyInstaller --clean --onefile --hidden-import=win32timezone --name="edge_print_agent" windows_service.py

if %errorlevel% neq 0 (
    echo Error: PyInstaller build failed.
    exit /b 1
)

echo ===================================================
echo Build Successful!
echo Executable is located at: dist\edge_print_agent.exe
echo ===================================================
