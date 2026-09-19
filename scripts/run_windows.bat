@echo off
REM Run alexa2anylist natively on Windows (no Docker)

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"

set "CONFIG_PATH=%ROOT_DIR%\config"

REM Generate protobuf module if missing
if not exist "%ROOT_DIR%\pcov_pb2.py" (
    python -m grpc_tools.protoc --proto_path="%ROOT_DIR%" --python_out="%ROOT_DIR%" "%ROOT_DIR%\pcov.proto"
)

python "%ROOT_DIR%\server.py"
