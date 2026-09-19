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
    login_calls = 0

    def __init__(self, *args, **kwargs):
        FakeAnyList.instances += 1
        self.args = args
        self.kwargs = kwargs

    def login(self):
        FakeAnyList.login_calls += 1
        return None

    def get_list_by_name(self, name):
        return types.SimpleNamespace(name=name, items=[])

    def teardown(self):
        FakeAnyList.teardown_calls += 1


class FakeAlexaAPI:
    instances = []
    login_calls = 0
    fail_first_login = False

    def __init__(self, *args, **kwargs):
        FakeAlexaAPI.instances.append(self)
        self.args = args
        self.kwargs = kwargs

    def login(self):
        FakeAlexaAPI.login_calls += 1
        if FakeAlexaAPI.fail_first_login and FakeAlexaAPI.login_calls == 1:
            raise RuntimeError("Amazon temporarily unavailable")


class FakeAlexaShoppingList:
    def __init__(self, api):
        self.api = api


class FakeSynchronizer:
    instances = 0
    sync_calls = 0
    fail_first_sync = True

    def __init__(self, anylist, alexa, journal_file=None):
        FakeSynchronizer.instances += 1
        FakeSynchronizer.last = self
        self.anylist = anylist
        self.alexa = alexa
        self.journal_file = journal_file

    def sync(self):
        FakeSynchronizer.sync_calls += 1
        if FakeSynchronizer.fail_first_sync and FakeSynchronizer.sync_calls == 1:
            raise RuntimeError("transient sync failure")


class ServerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original_modules = {
            module_name: sys.modules.get(module_name)
            for module_name in ("anylist", "alexa_api", "synchronizer", "server")
        }
        for module_name in ("anylist", "alexa_api", "synchronizer", "server"):
            sys.modules.pop(module_name, None)

        fake_anylist_module = types.ModuleType("anylist")
        fake_anylist_module.AnyList = FakeAnyList
        sys.modules["anylist"] = fake_anylist_module

        fake_alexa_module = types.ModuleType("alexa_api")
        fake_alexa_module.AlexaAPI = FakeAlexaAPI
        fake_alexa_module.AlexaShoppingList = FakeAlexaShoppingList
        sys.modules["alexa_api"] = fake_alexa_module

        fake_sync_module = types.ModuleType("synchronizer")
        fake_sync_module.Synchronizer = FakeSynchronizer
        sys.modules["synchronizer"] = fake_sync_module

        self.server = importlib.import_module("server")
        test_config = {
            "amazon_url": "amazon.co.uk",
            "amazon_username": "user@example.com",
            "amazon_password": "secret-password",
            "amazon_mfa_secret": "mfa-secret",
            "anylist_username": "anylist@example.com",
            "anylist_password": "anylist-password",
            "anylist_list_name": "Groceries",
        }
        # main() reloads the config from disk
        self.server._load_config = lambda: dict(test_config)

        FakeAnyList.instances = 0
        FakeAnyList.teardown_calls = 0
        FakeAnyList.login_calls = 0
        FakeAlexaAPI.instances = []
        FakeAlexaAPI.login_calls = 0
        FakeAlexaAPI.fail_first_login = False
        FakeSynchronizer.instances = 0
        FakeSynchronizer.sync_calls = 0
        FakeSynchronizer.fail_first_sync = True

    def tearDown(self):
        for module_name, original_module in self.original_modules.items():
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module

    def test_main_retries_after_sync_exception(self):
        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        self.server.sleep = fake_sleep

        self.server.main(max_cycles=2, retry_delay=0, sync_delay=0)

        self.assertGreaterEqual(FakeSynchronizer.instances, 2, "expected a fresh synchronizer after recovery")
        self.assertGreaterEqual(len(FakeAlexaAPI.instances), 2, "expected Alexa client recreation after recovery")
        self.assertIn(0, sleep_calls)

    def test_main_retries_after_anylist_login_exception(self):
        sleep_calls = []
        original_login = FakeAnyList.login

        def failing_first_login(self):
            FakeAnyList.login_calls += 1
            if FakeAnyList.login_calls == 1:
                raise RuntimeError("AnyList temporarily unavailable")

        self.server.sleep = lambda seconds: sleep_calls.append(seconds)
        FakeAnyList.login = failing_first_login
        FakeSynchronizer.fail_first_sync = False
        try:
            self.server.main(max_cycles=2, retry_delay=0, sync_delay=0)
        finally:
            FakeAnyList.login = original_login

        self.assertEqual(FakeAnyList.login_calls, 2)
        self.assertEqual(FakeSynchronizer.sync_calls, 1)
        self.assertIn(0, sleep_calls)

    def test_alexa_client_gets_config_and_credential_cache(self):
        FakeSynchronizer.fail_first_sync = False

        self.server.main(max_cycles=1, retry_delay=0, sync_delay=0)

        alexa = FakeAlexaAPI.instances[0]
        self.assertEqual(alexa.args, ("amazon.co.uk", "user@example.com", "secret-password", "mfa-secret"))
        self.assertEqual(alexa.kwargs, {"credential_cache": "alexa-credentials.json"})
        self.assertIs(FakeSynchronizer.last.alexa.api, alexa)

    def test_main_retries_after_alexa_login_exception(self):
        self.server.sleep = lambda seconds: None
        FakeAlexaAPI.fail_first_login = True
        FakeSynchronizer.fail_first_sync = False

        self.server.main(max_cycles=2, retry_delay=0, sync_delay=0)

        self.assertEqual(FakeAlexaAPI.login_calls, 2)
        self.assertEqual(FakeSynchronizer.sync_calls, 1)
        # The AnyList session from the failed attempt was closed, not leaked
        self.assertEqual(FakeAnyList.instances, 2)
        self.assertEqual(FakeAnyList.teardown_calls, 2)


if __name__ == "__main__":
    unittest.main()
