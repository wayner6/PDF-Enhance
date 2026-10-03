@echo off
setlocal
cd /d "%~dp0"

rem Run this on Windows with Python 3.12 installed (py launcher required).
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -e ".[dev]" "pyinstaller>=6"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 exit /b 1

rem Bundle ONNX Runtime native DLLs and RapidOCR models in BOTH distributions.
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name PDF-Enhance ^
  --collect-all rapidocr_onnxruntime ^
  --collect-all onnxruntime ^
  windows_entry.py
if errorlevel 1 exit /b 1

powershell -NoProfile -Command "Compress-Archive -Path 'dist\PDF-Enhance' -DestinationPath 'dist\PDF-Enhance-Windows.zip' -Force"
if errorlevel 1 exit /b 1

".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name PDF-Enhance ^
  --collect-all rapidocr_onnxruntime ^
  --collect-all onnxruntime ^
  windows_entry.py
if errorlevel 1 exit /b 1

echo Builds ready: dist\PDF-Enhance.exe and dist\PDF-Enhance-Windows.zip
endlocal
