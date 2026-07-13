from __future__ import annotations

import importlib
import sys
import types
import unittest

from tests.test_support import install_runtime_stubs


install_runtime_stubs()


class FakeAnyList:
    instances = 0
    teardown_calls = 0

    def __init__(self, *args, **kwargs):
        FakeAnyList.instances += 1
        self.args = args
        self.kwargs = kwargs

    def login(self):
        return None

    def get_list_by_name(self, name):
        return types.SimpleNamespace(name=name, items=[])

    def teardown(self):
        FakeAnyList.teardown_calls += 1


class FakeAlexa:
    instances = 0
    login_calls = []
    clear_calls = 0

    def __init__(self, *args, **kwargs):
        FakeAlexa.instances += 1
        self.args = args
        self.kwargs = kwargs
        self.is_authenticated = False

    def login(self, email, password, mfa_secret=None):
        FakeAlexa.login_calls.append((email, password, mfa_secret))
        self.is_authenticated = True

    def login_requires_mfa(self):
        return False

    def submit_mfa(self, code):
        self.is_authenticated = True

    def _clear_driver(self):
        FakeAlexa.clear_calls += 1


class FakeSynchronizer:
    instances = 0
    sync_calls = 0

    def __init__(self, anylist, alexa, journal_file=None):
        FakeSynchronizer.instances += 1
        self.anylist = anylist
        self.alexa = alexa
        self.journal_file = journal_file

    def sync(self):
        FakeSynchronizer.sync_calls += 1
        if FakeSynchronizer.sync_calls == 1:
            raise RuntimeError("transient sync failure")


class ServerRecoveryTests(unittest.TestCase):
    def setUp(self):
        for module_name in ("anylist", "alexa", "synchronizer", "server"):
            sys.modules.pop(module_name, None)

        fake_anylist_module = types.ModuleType("anylist")
        fake_anylist_module.AnyList = FakeAnyList
        sys.modules["anylist"] = fake_anylist_module

        fake_alexa_module = types.ModuleType("alexa")
        fake_alexa_module.AlexaShoppingList = FakeAlexa
        sys.modules["alexa"] = fake_alexa_module

        fake_sync_module = types.ModuleType("synchronizer")
        fake_sync_module.Synchronizer = FakeSynchronizer
        sys.modules["synchronizer"] = fake_sync_module

        self.server = importlib.import_module("server")
        self.server.config = {
            "amazon_url": "amazon.co.uk",
            "amazon_username": "user@example.com",
            "amazon_password": "secret-password",
            "amazon_mfa_secret": "mfa-secret",
            "anylist_username": "anylist@example.com",
            "anylist_password": "anylist-password",
            "anylist_list_name": "Groceries",
        }

        FakeAnyList.instances = 0
        FakeAnyList.teardown_calls = 0
        FakeAlexa.instances = 0
        FakeAlexa.login_calls = []
        FakeAlexa.clear_calls = 0
        FakeSynchronizer.instances = 0
        FakeSynchronizer.sync_calls = 0

    def test_main_retries_after_sync_exception(self):
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        self.server.sleep = fake_sleep

        self.server.main(max_cycles=2, retry_delay=0, sync_delay=0)

        self.assertGreaterEqual(FakeSynchronizer.instances, 2, "expected a fresh synchronizer after recovery")
        self.assertGreaterEqual(FakeAlexa.instances, 2, "expected Alexa client recreation after recovery")
        self.assertIn(0, sleep_calls)


if __name__ == "__main__":
    unittest.main()
