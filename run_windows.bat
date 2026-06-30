@echo off
REM Run alexa2anylist natively on Windows (no Docker)
REM Set HEADED=1 to open a visible browser window for debugging
REM Set SCREENSHOT_PATH to control where screenshots are saved (default: .\screenshots\)

set CONFIG_PATH=%~dp0config

REM Uncomment the line below to run in headed (visible) browser mode:
REM set HEADED=1

REM Generate protobuf module if missing
if not exist "%~dp0pcov_pb2.py" (
    python -m grpc_tools.protoc --proto_path="%~dp0" --python_out="%~dp0" "%~dp0pcov.proto"
)

python "%~dp0server.py"