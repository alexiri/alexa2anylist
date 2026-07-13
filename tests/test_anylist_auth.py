from __future__ import annotations

import unittest
import logging
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from tests.test_support import install_runtime_stubs


install_runtime_stubs()

from anylist import AnyList
from anylist import Item
from anylist import List
from synchronizer import Synchronizer


class _Response:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class _FakeMetadata:
    def __init__(self):
        self.operationId = ""
        self.handlerId = ""
        self.userId = ""


class _FakeProtoOp:
    def __init__(self):
        self.listId = ""
        self.listItemId = ""
        self.updatedValue = ""
        self.metadata = type("MetadataField", (), {"CopyFrom": lambda self, value: None})()


class _FakeProtoOpList:
    def __init__(self):
        self.operations = []


class _FakeThread:
    def __init__(self, *args, **kwargs):
        self.target = kwargs.get("target")
        self.kwargs = kwargs.get("kwargs", {})
        self.daemon = False
        self.started = False
        self.join_calls = 0

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        self.join_calls += 1


class _FakeWebSocket:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1

    def run_forever(self, *args, **kwargs):
        return None


class AnyListAuthRetryTests(unittest.TestCase):
    @patch("anylist.time.sleep", return_value=None)
    @patch("anylist.requests.post")
    def test_post_retries_with_refreshed_bearer_token(self, mock_post, _mock_sleep):
        api = AnyList("user@example.com", "password")
        api.client_id = "client-id"
        api.access_token = "stale-token"
        api.refresh_token = "refresh-token"

        mock_post.side_effect = [
            _Response(status_code=401, text="unauthorized"),
            _Response(
                status_code=200,
                json_data={
                    "access_token": "fresh-token",
                    "refresh_token": "fresh-refresh-token",
                },
            ),
            _Response(status_code=200, text="ok"),
        ]

        response = api._post("/data/test", data={"name": "milk"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_post.call_count, 3)

        first_call = mock_post.call_args_list[0]
        refresh_call = mock_post.call_args_list[1]
        retry_call = mock_post.call_args_list[2]

        self.assertEqual(first_call.kwargs["headers"]["Authorization"], "Bearer stale-token")
        self.assertEqual(refresh_call.kwargs["data"]["refresh_token"], "refresh-token")
        self.assertEqual(retry_call.kwargs["headers"]["Authorization"], "Bearer fresh-token")

    def test_list_refresh_forces_remote_reload(self):
        calls = []
        refreshed_list = object()

        class FakeApi:
            def get_lists(self, refresh_cache=False):
                calls.append(refresh_cache)

            def get_list_by_id(self, identifier):
                self.identifier = identifier
                return refreshed_list

        list_data = type(
            "ListData",
            (),
            {"identifier": "list-id", "name": "Groceries", "items": [], "creator": "user-id"},
        )()

        result = List(FakeApi(), list_data).refresh()

        self.assertIs(result, refreshed_list)
        self.assertEqual(calls, [True])

    def test_list_refresh_raises_when_list_missing_after_reload(self):
        class FakeApi:
            def get_lists(self, refresh_cache=False):
                return None

            def get_list_by_id(self, identifier):
                return None

        list_data = type(
            "ListData",
            (),
            {"identifier": "list-id", "name": "Groceries", "items": [], "creator": "user-id"},
        )()

        with self.assertRaisesRegex(Exception, "Failed to refresh list list-id"):
            List(FakeApi(), list_data).refresh()

    def test_item_save_rolls_back_local_checked_state_after_failed_update(self):
        class FakeApi:
            def __init__(self):
                self.refresh_calls = 0

            def _refresh_tokens(self):
                self.refresh_calls += 1

            def _sanitize_response_text(self, text, max_length=240):
                return text

        class FakeList:
            def __init__(self):
                self._api = FakeApi()
                self.calls = 0

            def _execute(self, ops):
                self.calls += 1
                return _Response(status_code=401, text="unauthorized")

        item_data = type(
            "ItemData",
            (),
            {
                "identifier": "item-id",
                "listId": "list-id",
                "name": "Yogures bio",
                "quantity": "",
                "details": "",
                "checked": True,
                "category": "",
                "userId": "user-id",
                "categoryMatchId": "",
                "manualSortIndex": 0,
            },
        )()

        item = Item(FakeList(), item_data)
        item.checked = False

        with patch("anylist.pcov_pb2.PBListOperationList", _FakeProtoOpList), \
             patch("anylist.pcov_pb2.PBListOperation", _FakeProtoOp), \
             patch("anylist.pcov_pb2.PBOperationMetadata", _FakeMetadata):
            with self.assertRaisesRegex(Exception, "Failed to update item"):
                item.save()

        self.assertTrue(item.checked)
        self.assertEqual(item._fieldsToUpdate, [])
        self.assertEqual(item._original_values, {})

    def test_item_save_sanitizes_error_text(self):
        class FakeApi:
            def _refresh_tokens(self):
                return None

            def _sanitize_response_text(self, text, max_length=240):
                return "sanitized"

        class FakeList:
            def __init__(self):
                self._api = FakeApi()

            def _execute(self, ops):
                return _Response(status_code=401, text="raw unauthorized body")

        item_data = type(
            "ItemData",
            (),
            {
                "identifier": "item-id",
                "listId": "list-id",
                "name": "Yogures bio",
                "quantity": "",
                "details": "",
                "checked": True,
                "category": "",
                "userId": "user-id",
                "categoryMatchId": "",
                "manualSortIndex": 0,
            },
        )()

        item = Item(FakeList(), item_data)
        item.checked = False

        with patch("anylist.pcov_pb2.PBListOperationList", _FakeProtoOpList), \
             patch("anylist.pcov_pb2.PBListOperation", _FakeProtoOp), \
             patch("anylist.pcov_pb2.PBOperationMetadata", _FakeMetadata):
            with self.assertRaisesRegex(Exception, "sanitized"):
                item.save()

    def test_synchronizer_get_fresh_lists_forces_alexa_reload(self):
        calls = []
        syncer = Synchronizer.__new__(Synchronizer)
        syncer.log = logging.getLogger("test-synchronizer")
        syncer._show_lists = lambda *args, **kwargs: None
        syncer.anylist = type("AnyListApi", (), {"refresh": lambda self: ["anylist"]})()
        syncer.alexa = type(
            "AlexaApi",
            (),
            {"get_alexa_list": lambda self, refresh=True: calls.append(refresh) or ["alexa"]},
        )()

        anylist_items, alexa_items = syncer._get_fresh_lists()

        self.assertEqual(anylist_items, ["anylist"])
        self.assertEqual(alexa_items, ["alexa"])
        self.assertEqual(calls, [True])

    def test_commit_transaction_skips_missing_anylist_items(self):
        syncer = Synchronizer.__new__(Synchronizer)
        syncer.log = logging.getLogger("test-synchronizer")
        syncer._refresh_baselines = lambda: None
        syncer._alexa_list = []
        syncer._old_anylist_list = []
        syncer._journal = type(
            "Journal",
            (),
            {
                "is_dirty": True,
                "get": lambda self, key: ["missing-id"] if key == Synchronizer.JOURNAL_KEY_ANYLIST_NEW_ITEMS else [],
                "reset": lambda self: None,
                "save": lambda self: None,
            },
        )()
        syncer._anylist_list = type("AnyListList", (), {"get_item_by_id": lambda self, identifier: None, "get_item_by_name": lambda self, name: None})()
        syncer.alexa = type(
            "AlexaApi",
            (),
            {
                "add_alexa_list_item": lambda self, name: (_ for _ in ()).throw(AssertionError("should not add")),
                "remove_alexa_list_item": lambda self, name: (_ for _ in ()).throw(AssertionError("should not remove")),
                "update_alexa_list_item": lambda self, old, new: (_ for _ in ()).throw(AssertionError("should not update")),
            },
        )()

        syncer._commit_transaction()

    def test_commit_transaction_verifies_alexa_add_postcondition(self):
        syncer = Synchronizer.__new__(Synchronizer)
        syncer.log = logging.getLogger("test-synchronizer")
        syncer._refresh_baselines = lambda: None
        syncer._alexa_list = []
        syncer._old_anylist_list = []
        syncer._journal = type(
            "Journal",
            (),
            {
                "is_dirty": True,
                "get": lambda self, key: ["item-id"] if key == Synchronizer.JOURNAL_KEY_ANYLIST_NEW_ITEMS else [],
                "reset": lambda self: None,
                "save": lambda self: None,
            },
        )()
        syncer._anylist_list = type(
            "AnyListList",
            (),
            {
                "get_item_by_id": lambda self, identifier: type("ItemObj", (), {"name": "Milk", "identifier": identifier})(),
                "get_item_by_name": lambda self, name: None,
            },
        )()
        syncer.alexa = type(
            "AlexaApi",
            (),
            {
                "add_alexa_list_item": lambda self, name: [],
                "remove_alexa_list_item": lambda self, name: [],
                "update_alexa_list_item": lambda self, old, new: [],
            },
        )()

        with self.assertRaisesRegex(Exception, "item not present after update"):
            syncer._commit_transaction()

    def test_sync_seeds_baseline_while_in_sync_so_anylist_check_removes_from_alexa(self):
        class FakeItem:
            def __init__(self, identifier, name, checked=False):
                self.identifier = identifier
                self.name = name
                self.checked = checked

            def __eq__(self, other):
                return (
                    getattr(other, "identifier", None) == self.identifier
                    and getattr(other, "name", None) == self.name
                    and getattr(other, "checked", None) == self.checked
                )

        class FakeAnyListState:
            def __init__(self, items):
                self.items = items

            def __iter__(self):
                yield from self.items

            def __contains__(self, item):
                return self.get_item_by_id(item.identifier) is not None

            def get_item_by_id(self, identifier):
                return next((item for item in self.items if item.identifier == identifier), None)

            def get_item_by_name(self, name):
                return next((item for item in self.items if item.name == name), None)

            def add_or_uncheck_item(self, item):
                raise AssertionError("should remove from Alexa, not uncheck on AnyList")

            def check_item(self, item):
                raise AssertionError("should not check AnyList item")

        anylist_states = [
            FakeAnyListState([FakeItem("item-1", "Milk", checked=False)]),
            FakeAnyListState([FakeItem("item-1", "Milk", checked=False)]),
            FakeAnyListState([FakeItem("item-1", "Milk", checked=True)]),
            FakeAnyListState([FakeItem("item-1", "Milk", checked=True)]),
        ]
        alexa_states = [
            ["Milk"],
            ["Milk"],
            ["Milk"],
            [],
        ]

        class FakeAnyListApi:
            def __init__(self):
                self.calls = 0

            def refresh(self):
                state = anylist_states[self.calls]
                self.calls += 1
                return state

        class FakeAlexaApi:
            def __init__(self):
                self.refresh_calls = 0
                self.remove_calls = []

            def get_alexa_list(self, refresh=True):
                state = alexa_states[self.refresh_calls]
                self.refresh_calls += 1
                return list(state)

            def remove_alexa_list_item(self, item):
                self.remove_calls.append(item)
                return []

            def add_alexa_list_item(self, item):
                raise AssertionError("should not add Alexa item")

            def update_alexa_list_item(self, old, new):
                raise AssertionError("should not rename Alexa item")

        syncer = Synchronizer(FakeAnyListApi(), FakeAlexaApi())

        self.assertEqual(syncer._old_alexa_list, ["Milk"])

        syncer.sync()
        self.assertEqual(syncer._old_alexa_list, ["Milk"])

        syncer.sync()

        self.assertEqual(syncer.alexa.remove_calls, ["Milk"])

    def test_teardown_is_safe_when_websocket_never_initialized(self):
        api = AnyList("user@example.com", "password")
        api.ws = None

        api.teardown()

    @patch("anylist.threading.Thread", _FakeThread)
    @patch("anylist.websocket.WebSocketApp")
    def test_refresh_tokens_forces_websocket_reconnect(self, mock_ws_app):
        ws_instances = []

        def ws_factory(*args, **kwargs):
            ws = _FakeWebSocket()
            ws_instances.append(ws)
            return ws

        mock_ws_app.side_effect = ws_factory

        api = AnyList("user@example.com", "password")
        api.client_id = "client-id"
        api.access_token = "stale"
        api.refresh_token = "refresh"

        api._setup_websocket()
        self.assertEqual(len(ws_instances), 1)

        with patch("anylist.requests.post") as mock_post:
            mock_post.return_value = _Response(
                status_code=200,
                json_data={"access_token": "fresh", "refresh_token": "fresh-refresh"},
            )
            api._refresh_tokens()

        self.assertEqual(len(ws_instances), 2)
        self.assertEqual(ws_instances[0].close_calls, 1)

    def test_save_credentials_writes_private_file_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("anylist.os.environ", {"CONFIG_PATH": tmpdir}):
                api = AnyList("user@example.com", "password", credential_cache="creds.json")
                api.client_id = "client-id"
                api.access_token = "token"
                api.refresh_token = "refresh"

                api._save_credentials(method="test")

                path = Path(tmpdir) / "creds.json"
                mode = path.stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()