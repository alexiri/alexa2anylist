@echo off
REM Run alexa2anylist natively on Windows (no Docker)
REM Set HEADED=1 to open a visible browser window for debugging
REM Set SCREENSHOT_PATH to control where screenshots are saved (default: .\screenshots\)

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

set "CONFIG_PATH=%ROOT_DIR%\config"

REM Uncomment the line below to run in headed (visible) browser mode:
REM set HEADED=1

REM Generate protobuf module if missing
if not exist "%ROOT_DIR%\pcov_pb2.py" (
    python -m grpc_tools.protoc --proto_path="%ROOT_DIR%" --python_out="%ROOT_DIR%" "%ROOT_DIR%\pcov.proto"
)

python "%ROOT_DIR%\server.py"
