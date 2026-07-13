from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_support import install_runtime_stubs


install_runtime_stubs()

import alexa  # noqa: E402


class FakeDriver:
    def __init__(self, cookies=None, current_url="https://www.amazon.co.uk"):
        self.cookies = cookies or [{"name": "session", "value": "abc"}]
        self.current_url = current_url
        self.added_cookies = []
        self.refresh_calls = 0
        self.find_element_calls = []
        self.find_elements_calls = []

    def get_cookies(self):
        return list(self.cookies)

    def add_cookie(self, cookie):
        self.added_cookies.append(cookie)

    def refresh(self):
        self.refresh_calls += 1

    def find_element(self, by, value):
        self.find_element_calls.append((by, value))
        return types.SimpleNamespace(get_attribute=lambda name: "https://www.amazon.co.uk/ap/signin")

    def find_elements(self, by, value):
        self.find_elements_calls.append((by, value))
        if value == "nav-link-accountList":
            return [object()]
        return []


class FakeButton:
    def __init__(self, stale_on_click=False):
        self.stale_on_click = stale_on_click
        self.click_calls = 0

    def click(self):
        self.click_calls += 1
        if self.stale_on_click:
            raise alexa.StaleElementReferenceException("stale element")


class FakeInput:
    def __init__(self):
        self.clear_calls = 0
        self.sent_values = []

    def clear(self):
        self.clear_calls += 1

    def send_keys(self, value):
        self.sent_values.append(value)


class FakeActionContainer:
    def __init__(self, button):
        self.button = button

    def find_element(self, by, value):
        if value != "button":
            raise AssertionError(f"unexpected nested lookup: {by}/{value}")
        return self.button


class FakeInputContainer:
    def __init__(self, textfield):
        self.textfield = textfield

    def find_element(self, by, value):
        if value != "input":
            raise AssertionError(f"unexpected nested lookup: {by}/{value}")
        return self.textfield


class FakeListItemElement:
    def __init__(self, edit_button=None, save_button=None, delete_button=None, textfield=None):
        self.edit_button = edit_button
        self.save_button = save_button
        self.delete_button = delete_button
        self.textfield = textfield

    def find_element(self, by, value):
        if value == "item-actions-1":
            return FakeActionContainer(self.edit_button)
        if value == "item-actions-2":
            button = self.save_button if self.save_button is not None else self.delete_button
            return FakeActionContainer(button)
        if value == "input-box":
            return FakeInputContainer(self.textfield)
        raise AssertionError(f"unexpected lookup: {by}/{value}")


class AlexaReliabilityTests(unittest.TestCase):
    def test_save_session_writes_cookies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
            instance.cookies_path = tmpdir
            instance.is_authenticated = True
            instance.driver = FakeDriver()

            instance._save_session()

            cookie_path = Path(tmpdir) / "cookies.json"
            self.assertTrue(cookie_path.exists(), "expected cookies to be written to disk")
            self.assertEqual(json.loads(cookie_path.read_text()), instance.driver.get_cookies())

    def test_login_keeps_credentials_and_auto_submits_mfa(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance.amazon_url = "amazon.co.uk"
        instance.driver = FakeDriver()
        instance.is_authenticated = False
        instance._selenium_get = lambda *args, **kwargs: None
        instance._driver_is_on_login_email_page = lambda: True
        instance._handle_login_email_page = lambda: None
        instance._driver_is_on_login_password_page = lambda: True
        instance._handle_login_password_page = lambda: None
        instance._selenium_wait_page_ready = lambda: None
        mfa_state = {"required": True}
        submitted_codes = []

        def fake_login_requires_mfa():
            return mfa_state["required"]

        def fake_submit_mfa(code):
            submitted_codes.append(code)
            mfa_state["required"] = False
            instance.is_authenticated = True

        instance.login_requires_mfa = fake_login_requires_mfa
        instance.submit_mfa = fake_submit_mfa
        instance._login_successful = lambda: setattr(instance, "is_authenticated", True)

        instance.login("user@example.com", "secret-password", "mfa-secret")

        self.assertEqual(submitted_codes, ["654321"])
        self.assertEqual(instance.email, "user@example.com")
        self.assertEqual(instance.password, "secret-password")
        self.assertEqual(instance.mfa_secret, "mfa-secret")
        self.assertTrue(instance.is_authenticated)

    def test_ensure_driver_on_list_reauthenticates_when_session_lost(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance.amazon_url = "amazon.co.uk"
        instance.driver = FakeDriver(current_url="https://www.amazon.co.uk/alexaquantum/sp/alexaShoppingList?ref=nav_asl")
        instance.email = "user@example.com"
        instance.password = "secret-password"
        instance.mfa_secret = "mfa-secret"
        instance.requires_login = lambda: True
        login_calls = []

        def fake_login(email, password, mfa_secret=None):
            login_calls.append((email, password, mfa_secret))
            instance.is_authenticated = True

        instance.login = fake_login
        instance._selenium_get = lambda *args, **kwargs: None
        instance._selenium_wait_element = lambda *args, **kwargs: None

        instance._ensure_driver_is_on_alexa_list(refresh=False)

        self.assertEqual(login_calls, [("user@example.com", "secret-password", "mfa-secret")])

    def test_remove_alexa_list_item_retries_stale_element(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        stale_element = FakeListItemElement(delete_button=FakeButton(stale_on_click=True))
        fresh_element = FakeListItemElement(delete_button=FakeButton(stale_on_click=False))
        get_calls = {"count": 0}

        def fake_get_item(item):
            get_calls["count"] += 1
            return stale_element if get_calls["count"] == 1 else fresh_element

        instance._get_alexa_list_item_element = fake_get_item
        instance.get_alexa_list = lambda refresh: ["fresh list"]

        with patch("alexa.time.sleep", return_value=None):
            result = instance.remove_alexa_list_item("milk")

        self.assertEqual(result, ["fresh list"])
        self.assertEqual(get_calls["count"], 2)
        self.assertEqual(stale_element.delete_button.click_calls, 1)
        self.assertEqual(fresh_element.delete_button.click_calls, 1)

    def test_update_alexa_list_item_retries_stale_element(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        stale_textfield = FakeInput()
        fresh_textfield = FakeInput()
        stale_element = FakeListItemElement(
            edit_button=FakeButton(stale_on_click=False),
            save_button=FakeButton(stale_on_click=True),
            textfield=stale_textfield,
        )
        fresh_element = FakeListItemElement(
            edit_button=FakeButton(stale_on_click=False),
            save_button=FakeButton(stale_on_click=False),
            textfield=fresh_textfield,
        )
        get_calls = {"count": 0}

        def fake_get_item(item):
            get_calls["count"] += 1
            return stale_element if get_calls["count"] == 1 else fresh_element

        instance._get_alexa_list_item_element = fake_get_item
        instance.get_alexa_list = lambda refresh: ["renamed item"]

        with patch("alexa.time.sleep", return_value=None):
            result = instance.update_alexa_list_item("milk", "oat milk")

        self.assertEqual(result, ["renamed item"])
        self.assertEqual(get_calls["count"], 2)
        self.assertEqual(stale_textfield.sent_values, ["oat milk"])
        self.assertEqual(fresh_textfield.sent_values, ["oat milk"])
        self.assertEqual(fresh_textfield.clear_calls, 1)


if __name__ == "__main__":
    unittest.main()
