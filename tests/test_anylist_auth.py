from __future__ import annotations

import unittest
import logging
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

    def test_item_save_rolls_back_local_checked_state_after_failed_update(self):
        class FakeApi:
            def __init__(self):
                self.refresh_calls = 0

            def _refresh_tokens(self):
                self.refresh_calls += 1

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


if __name__ == "__main__":
    unittest.main()