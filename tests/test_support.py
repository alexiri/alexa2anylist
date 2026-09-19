from __future__ import annotations

import sys
import types


def install_runtime_stubs() -> None:
    if "websocket" not in sys.modules:
        websocket = types.ModuleType("websocket")

        class WebSocketApp:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def run_forever(self, *args, **kwargs):
                return None

            def close(self):
                return None

        class WebSocketConnectionClosedException(Exception):
            pass

        websocket.WebSocketApp = WebSocketApp
        websocket.WebSocketConnectionClosedException = WebSocketConnectionClosedException
        sys.modules["websocket"] = websocket

    if "pcov_pb2" not in sys.modules:
        pcov_pb2 = types.ModuleType("pcov_pb2")

        class _Message:
            def __init__(self, *args, **kwargs):
                pass

            def SerializeToString(self):
                return b""

            def ParseFromString(self, data):
                return None

        pcov_pb2.PBUserDataResponse = _Message
        pcov_pb2.PBListOperation = _Message
        pcov_pb2.PBOperationMetadata = _Message
        pcov_pb2.PBListOperationList = _Message
        sys.modules["pcov_pb2"] = pcov_pb2

    if "onetimepass" not in sys.modules:
        onetimepass = types.ModuleType("onetimepass")

        def get_totp(secret):
            return "654321"

        onetimepass.get_totp = get_totp
        sys.modules["onetimepass"] = onetimepass
