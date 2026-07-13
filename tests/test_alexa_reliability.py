from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_support import install_runtime_stubs


install_runtime_stubs()

import alexa  # noqa: E402


class FakeClickTarget:
    def __init__(self, stale_on_click=False):
        self.stale_on_click = stale_on_click
        self.click_calls = 0

    def click(self):
        self.click_calls += 1
        if self.stale_on_click:
            raise alexa.StaleElementReferenceException("stale element")


class FakeInput:
    def __init__(self):
        self.fill_values = []

    def fill(self, value):
        self.fill_values.append(value)


class FakeElement:
    def __init__(self, edit_button=None, save_or_delete_button=None, textfield=None):
        self.edit_button = edit_button
        self.save_or_delete_button = save_or_delete_button
        self.textfield = textfield or FakeInput()

    def locator(self, selector):
        if selector == '.item-actions-1 button':
            return self.edit_button
        if selector == '.item-actions-2 button':
            return self.save_or_delete_button
        if selector == '.input-box input':
            return self.textfield
        raise AssertionError(f"unexpected selector: {selector}")


class FakePage:
    def __init__(self, url="https://www.amazon.co.uk"):
        self.url = url
        self.goto_calls = []
        self.reload_calls = 0
        self.wait_selector_calls = []
        self.wait_load_state_calls = []
        self.get_attribute_calls = []

    def goto(self, url, wait_until=None):
        self.goto_calls.append((url, wait_until))
        self.url = url

    def wait_for_selector(self, selector, timeout=None):
        self.wait_selector_calls.append((selector, timeout))
        return None

    def reload(self, wait_until=None):
        self.reload_calls += 1
        return None

    def wait_for_load_state(self, state):
        self.wait_load_state_calls.append(state)
        return None

    def get_attribute(self, selector, attr):
        self.get_attribute_calls.append((selector, attr))
        return "https://www.amazon.co.uk/ap/signin"

    def locator(self, selector):
        if selector == '.nav-action-signin-button':
            return type("Locator", (), {"count": lambda self: 0})()
        return type("Locator", (), {"count": lambda self: 1})()


class FakeContext:
    def __init__(self, cookies=None):
        self._cookies = cookies or [{"name": "session", "value": "abc"}]

    def cookies(self):
        return list(self._cookies)


class AlexaReliabilityTests(unittest.TestCase):
    def test_save_session_writes_cookies(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
            instance.cookies_path = tmpdir
            instance.is_authenticated = True
            instance._context = FakeContext()

            instance._save_session()

            cookie_path = Path(tmpdir) / "cookies.json"
            self.assertTrue(cookie_path.exists(), "expected cookies to be written to disk")
            self.assertEqual(json.loads(cookie_path.read_text()), instance._context.cookies())

    def test_save_session_writes_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
            instance.cookies_path = tmpdir
            instance.is_authenticated = True
            instance._context = FakeContext()

            instance._save_session()

            mode = (Path(tmpdir) / "cookies.json").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_login_marks_success_when_auth_pages_complete_without_mfa(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance.amazon_url = "amazon.co.uk"
        instance._page = FakePage(url="https://www.amazon.co.uk")
        instance.is_authenticated = False
        instance._driver_is_on_login_email_page = lambda: True
        instance._handle_login_email_page = lambda: None
        instance._driver_is_on_login_password_page = lambda: True
        instance._handle_login_password_page = lambda: setattr(instance._page, "url", "https://www.amazon.co.uk")
        instance.login_requires_mfa = lambda: False
        instance._login_successful = lambda: setattr(instance, "is_authenticated", True)

        instance.login("user@example.com", "secret-password")

        self.assertTrue(instance.is_authenticated)

    def test_ensure_on_alexa_list_reloads_when_refresh_requested(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance.amazon_url = "amazon.co.uk"
        instance._page = FakePage(url="https://www.amazon.co.uk/alexaquantum/sp/alexaShoppingList?ref=nav_asl")

        instance._ensure_on_alexa_list(refresh=True)

        self.assertEqual(instance._page.reload_calls, 1)

    def test_add_alexa_list_item_returns_current_list_when_item_exists(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance._get_alexa_list_item_element = lambda item: object()
        instance.get_alexa_list = lambda refresh: ["milk"]

        result = instance.add_alexa_list_item("milk")

        self.assertEqual(result, ["milk"])

    def test_update_alexa_list_item_returns_none_when_missing(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance._get_alexa_list_item_element = lambda item: None

        result = instance.update_alexa_list_item("old", "new")

        self.assertIsNone(result)

    def test_remove_alexa_list_item_returns_none_when_missing(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        instance._get_alexa_list_item_element = lambda item: None

        result = instance.remove_alexa_list_item("milk")

        self.assertIsNone(result)

    def test_remove_alexa_list_item_returns_refreshed_list_when_item_found(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        delete_button = FakeClickTarget()
        element = FakeElement(save_or_delete_button=delete_button)
        instance._get_alexa_list_item_element = lambda item: element
        instance.get_alexa_list = lambda refresh: ["fresh list"]

        with patch("alexa.time.sleep", return_value=None):
            result = instance.remove_alexa_list_item("milk")

        self.assertEqual(result, ["fresh list"])
        self.assertEqual(delete_button.click_calls, 1)

    def test_update_alexa_list_item_returns_refreshed_list_when_item_found(self):
        instance = alexa.AlexaShoppingList.__new__(alexa.AlexaShoppingList)
        input_field = FakeInput()
        edit_button = FakeClickTarget()
        save_button = FakeClickTarget()
        element = FakeElement(
            edit_button=edit_button,
            save_or_delete_button=save_button,
            textfield=input_field,
        )
        instance._get_alexa_list_item_element = lambda item: element
        instance.get_alexa_list = lambda refresh: ["renamed item"]

        with patch("alexa.time.sleep", return_value=None):
            result = instance.update_alexa_list_item("milk", "oat milk")

        self.assertEqual(result, ["renamed item"])
        self.assertEqual(input_field.fill_values, ["oat milk"])
        self.assertEqual(edit_button.click_calls, 1)
        self.assertEqual(save_button.click_calls, 1)


if __name__ == "__main__":
    unittest.main()
