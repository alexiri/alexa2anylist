"""End-to-end sync tests: the real Synchronizer driving the real Alexa client,
against a stateful fake of Amazon's lists API and a fake AnyList list."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
import uuid
from urllib.parse import urlparse
from unittest.mock import patch

import requests

from tests.test_alexa_api import FakeAmazon, LISTS, lists_response, query, respond
from tests.test_support import install_runtime_stubs


install_runtime_stubs()

import alexa_api
from alexa_api import AlexaAPI, AlexaError, AlexaShoppingList
from synchronizer import Synchronizer


class FakeAlexaLists:
    """Amazon's shopping list API, with version checks and paging like the real one."""

    def __init__(self, amazon, page_size=100):
        self.items = {}
        self.page_size = page_size
        self.writes = []
        self.fail_next = None
        amazon.route("POST", f"{LISTS}/fetch", lists_response())
        amazon.fallback = self.handle

    def add(self, name, status="ACTIVE"):
        item_id = uuid.uuid4().hex
        self.items[item_id] = {"itemId": item_id, "itemName": name, "itemStatus": status, "version": 1}
        return item_id

    def find(self, name):
        return next(i for i in self.items.values() if i["itemName"] == name)

    def complete(self, name):
        item = self.find(name)
        item["itemStatus"] = "COMPLETE"
        item["version"] += 1

    def delete(self, name):
        del self.items[self.find(name)["itemId"]]

    @property
    def active(self):
        return sorted(i["itemName"] for i in self.items.values() if i["itemStatus"] == "ACTIVE")

    @property
    def completed(self):
        return sorted(i["itemName"] for i in self.items.values() if i["itemStatus"] == "COMPLETE")

    def handle(self, request):
        parts = urlparse(request.url).path.split("/lists/")[1].split("/")
        if parts[0] != "shop-id" or parts[1] != "items":
            return respond(404)
        body = json.loads(request.body or "{}")

        if parts[2:] == ["fetch"]:
            return self._fetch(int(query(request)["limit"]), body.get("nextToken"))

        if self.fail_next is not None:
            response, self.fail_next = self.fail_next, None
            return response
        self.writes.append((request.method, parts[2:], body))

        if request.method == "POST" and len(parts) == 2:
            for new_item in body["items"]:
                self.add(new_item["itemName"])
            return respond(json_data={})

        item = self.items.get(parts[2])
        if item is None:
            return respond(404)
        if int(query(request)["version"]) != item["version"]:
            return respond(409, json_data={"message": "version conflict"})

        if request.method == "DELETE":
            del self.items[item["itemId"]]
        elif request.method == "PUT":
            for update in body["itemAttributesToUpdate"]:
                assert update["type"] in ("itemName", "itemStatus"), update
                item[update["type"]] = update["value"]
            item["version"] += 1
        return respond(json_data={})

    def _fetch(self, limit, token):
        items = list(self.items.values())
        start = int(token or 0)
        end = start + min(limit, self.page_size)
        page = {"itemInfoList": copy.deepcopy(items[start:end])}
        if end < len(items):
            page["nextToken"] = str(end)
        return respond(json_data=page)


class FakeAnyListItem:
    def __init__(self, name, checked=False, identifier=None):
        self.identifier = identifier or uuid.uuid4().hex
        self.name = name
        self.checked = checked


class FakeAnyListList:
    """Behaves like anylist.List: refresh() returns a new snapshot, changes go to the server."""

    def __init__(self, server=None):
        self.server = server if server is not None else {}
        self.items = [copy.copy(i) for i in self.server.values()]

    @classmethod
    def with_items(cls, *items):
        server = {}
        for item in items:
            name, checked = item if isinstance(item, tuple) else (item, False)
            new_item = FakeAnyListItem(name, checked)
            server[new_item.identifier] = new_item
        return cls(server)

    def refresh(self):
        return FakeAnyListList(self.server)

    def __iter__(self):
        yield from self.items

    def __contains__(self, item):
        return self.get_item_by_id(item.identifier) is not None

    def get_item_by_id(self, identifier):
        return next((i for i in self.items if i.identifier == identifier), None)

    def get_item_by_name(self, name):
        return next((i for i in self.items if i.name == name), None)

    def _set(self, item_name, **changes):
        for target in (self.get_item_by_name(item_name), self._server_item(item_name)):
            for key, value in changes.items():
                setattr(target, key, value)

    def _server_item(self, name):
        return next(i for i in self.server.values() if i.name == name)

    def check_item(self, name):
        self._set(name, checked=True)

    def add_or_uncheck_item(self, name):
        if self.get_item_by_name(name) is None:
            item = FakeAnyListItem(name)
            self.server[item.identifier] = item
            self.items.append(copy.copy(item))
        else:
            self._set(name, checked=False)

    # Changes made by the user in the AnyList app
    def user_add(self, name):
        item = FakeAnyListItem(name)
        self.server[item.identifier] = item

    def user_set(self, item_name, **changes):
        for key, value in changes.items():
            setattr(self._server_item(item_name), key, value)

    @property
    def unchecked(self):
        return sorted(i.name for i in self.server.values() if not i.checked)

    @property
    def checked(self):
        return sorted(i.name for i in self.server.values() if i.checked)


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"CONFIG_PATH": self._tmp.name}).start()
        patch.object(alexa_api.time, "sleep").start()

        with open(os.path.join(self._tmp.name, "alexa-credentials.json"), "w") as file:
            json.dump({
                "device_serial": "SERIAL",
                "refresh_token": "refresh",
                "cookies": [{"domain": ".amazon.es", "name": "session-token", "value": "session"}],
            }, file)

        self.amazon = FakeAmazon()
        self.alexa = FakeAlexaLists(self.amazon)
        session = requests.Session()
        session.mount("https://", self.amazon)
        self.api = AlexaAPI("amazon.es", "me@example.com", "pw", "SECRET",
                            credential_cache="alexa-credentials.json", session=session)
        self.api.login()

    def start(self, anylist):
        self.anylist = anylist
        self.syncer = Synchronizer(anylist, AlexaShoppingList(self.api))
        self.alexa.writes.clear()
        return self.syncer

    def assert_in_sync(self, active):
        self.assertEqual(self.alexa.active, sorted(active))
        self.assertEqual(self.anylist.unchecked, sorted(active))


class StartupTest(SyncTestCase):
    def test_startup_makes_alexa_match_anylist(self):
        self.alexa.add("Bread")
        self.alexa.add("Stale thing")
        self.alexa.add("Milk", status="COMPLETE")

        self.start(FakeAnyListList.with_items("Milk", ("Eggs", True), "Bread"))

        self.assert_in_sync(["Milk", "Bread"])
        # Completed Milk was brought back rather than duplicated
        self.assertEqual(len(self.alexa.items), 2)

    def test_startup_in_sync_makes_no_changes(self):
        self.alexa.add("Milk")
        self.alexa.add("Old", status="COMPLETE")
        self.start(FakeAnyListList.with_items("Milk", ("Old", True)))

        self.syncer.sync()

        self.assertEqual(self.alexa.writes, [])

    def test_large_lists_are_read_across_pages(self):
        self.alexa.page_size = 2
        names = [f"Item {n}" for n in range(7)]
        for name in names:
            self.alexa.add(name)
        for n in range(5):
            self.alexa.add(f"Done {n}", status="COMPLETE")

        self.start(FakeAnyListList.with_items(*names))
        self.syncer.sync()

        self.assertEqual(self.alexa.writes, [])


class AlexaChangesTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.alexa.add("Milk")
        self.alexa.add("Eggs")
        self.start(FakeAnyListList.with_items("Milk", "Eggs"))

    def test_voice_added_item_is_capitalized_and_added_to_anylist(self):
        self.alexa.add("apples")

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Eggs", "Apples"])

    def test_voice_added_item_has_each_word_capitalized(self):
        self.alexa.add("pan de molde")
        self.alexa.add("garbanzos 4.5kg")

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Eggs", "Pan De Molde", "Garbanzos 4.5kg"])

    def test_item_completed_in_alexa_app_is_checked_in_anylist(self):
        self.alexa.complete("Milk")

        self.syncer.sync()

        self.assertEqual(self.anylist.checked, ["Milk"])
        self.assertEqual(self.alexa.active, ["Eggs"])

    def test_item_deleted_in_alexa_app_is_checked_in_anylist(self):
        self.alexa.delete("Eggs")

        self.syncer.sync()

        self.assertEqual(self.anylist.checked, ["Eggs"])
        self.assertEqual(self.alexa.active, ["Milk"])

    def test_item_readded_by_voice_after_completion(self):
        self.alexa.complete("Milk")
        self.syncer.sync()
        self.alexa.add("milk")

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Eggs"])


class AnyListChangesTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.alexa.add("Milk")
        self.alexa.add("Eggs")
        self.start(FakeAnyListList.with_items("Milk", "Eggs", ("Cheese", True)))

    def test_added_item_goes_to_alexa(self):
        self.anylist.user_add("Bread")

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Eggs", "Bread"])

    def test_checked_item_is_deleted_from_alexa(self):
        self.anylist.user_set("Milk", checked=True)

        self.syncer.sync()

        self.assert_in_sync(["Eggs"])
        self.assertEqual(self.alexa.completed, [])

    def test_unchecked_item_is_reactivated_in_alexa_instead_of_duplicated(self):
        self.alexa.add("Cheese", status="COMPLETE")
        self.syncer.sync()

        self.anylist.user_set("Cheese", checked=False)
        self.syncer.sync()

        self.assert_in_sync(["Milk", "Eggs", "Cheese"])
        self.assertEqual(len(self.alexa.items), 3)

    def test_renamed_item_is_renamed_in_alexa(self):
        self.anylist.user_set("Milk", name="Oat milk")

        self.syncer.sync()

        self.assert_in_sync(["Oat milk", "Eggs"])
        self.assertEqual(len(self.alexa.items), 2)

    def test_deleted_item_is_removed_from_alexa(self):
        del self.anylist.server[self.anylist._server_item("Milk").identifier]

        self.syncer.sync()

        self.assert_in_sync(["Eggs"])

    def test_deleting_crossed_out_copy_leaves_active_item_on_alexa(self):
        self.anylist.user_add("Milk")
        self.anylist.user_set("Milk", checked=True)  # the old copy, as _server_item finds it first
        self.syncer.sync()
        crossed_out = next(i for i in self.anylist.server.values() if i.name == "Milk" and i.checked)
        self.alexa.writes.clear()

        # The deletion only reaches the journal when something else changed too
        del self.anylist.server[crossed_out.identifier]
        self.alexa.add("Bread")
        self.syncer.sync()

        self.assert_in_sync(["Eggs", "Milk", "Bread"])
        self.assertEqual(self.alexa.writes, [])

    def test_changes_on_both_sides_in_one_sync(self):
        self.anylist.user_add("Bread")
        self.anylist.user_set("Eggs", checked=True)
        self.alexa.add("iced coffee")

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Bread", "Iced Coffee"])


class FailureTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.alexa.add("Milk")
        self.start(FakeAnyListList.with_items("Milk"))

    def test_expired_cookies_are_renewed_mid_sync(self):
        self.amazon.route("POST", alexa_api.TOKEN_URL, respond(json_data={"response": {"tokens": {"cookies": {
            ".amazon.es": [{"Name": "session-token", "Value": "renewed"}],
        }}}}))
        self.anylist.user_add("Bread")
        self.alexa.fail_next = respond(401)

        self.syncer.sync()

        self.assert_in_sync(["Milk", "Bread"])
        self.assertIn("session-token=renewed", self.amazon.requests[-1].headers["Cookie"])

    def test_failed_write_raises_and_a_fresh_start_recovers(self):
        self.anylist.user_add("Bread")
        self.alexa.fail_next = respond(503, text="Service Unavailable")

        with self.assertRaises(AlexaError):
            self.syncer.sync()

        # server.py starts over with a new Synchronizer after any error
        self.start(self.anylist.refresh())
        self.assert_in_sync(["Milk", "Bread"])

    def test_concurrent_alexa_edit_fails_cleanly(self):
        self.anylist.user_set("Milk", checked=True)
        self.syncer.sync()
        self.alexa.add("Tea")
        self.syncer.sync()

        # Someone edits Tea in the Alexa app between our fetch and our write
        shopping = self.syncer.alexa._shopping_list()
        shopping.refresh()
        self.alexa.find("Tea")["version"] += 1

        with self.assertRaises(AlexaError):
            shopping.remove_item("Tea")

        self.assertEqual(self.alexa.active, ["Tea"])


class AdapterTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.adapter = AlexaShoppingList(self.api)

    def test_add_existing_active_item_is_a_no_op(self):
        self.alexa.add("Milk")
        self.adapter.get_alexa_list()
        self.alexa.writes.clear()

        self.assertEqual(self.adapter.add_alexa_list_item("Milk"), ["Milk"])
        self.assertEqual(self.alexa.writes, [])

    def test_missing_items_return_none(self):
        self.alexa.add("Milk", status="COMPLETE")

        self.assertIsNone(self.adapter.remove_alexa_list_item("Milk"))
        self.assertIsNone(self.adapter.remove_alexa_list_item("Nope"))
        self.assertIsNone(self.adapter.update_alexa_list_item("Nope", "Other"))
        self.assertEqual(self.alexa.writes, [])

    def test_get_without_refresh_uses_cached_list(self):
        self.alexa.add("Milk")
        self.assertEqual(self.adapter.get_alexa_list(), ["Milk"])
        self.alexa.add("Eggs")

        self.assertEqual(self.adapter.get_alexa_list(refresh=False), ["Milk"])
        self.assertEqual(self.adapter.get_alexa_list(refresh=True), ["Milk", "Eggs"])

    def test_account_without_shopping_list_raises(self):
        self.amazon.route("POST", f"{LISTS}/fetch", respond(json_data={"listInfoList": []}))

        with self.assertRaises(AlexaError):
            self.adapter.get_alexa_list()


if __name__ == "__main__":
    unittest.main()
