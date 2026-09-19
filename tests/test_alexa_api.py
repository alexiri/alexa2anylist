from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

import requests
from requests.adapters import BaseAdapter

from tests.test_support import install_runtime_stubs


install_runtime_stubs()

import alexa_api
from alexa_api import AlexaAPI, AlexaAuthError, AlexaError, AlexaItem


LISTS = "https://www.amazon.es/alexashoppinglists/api/v2/lists"
SIGNIN = "https://www.amazon.com/ap/signin"
MFA_SUBMIT = "https://www.amazon.com/ap/mfa-submit"
MAPLANDING = "https://www.amazon.com/ap/maplanding"

SIGNIN_PAGE = """
<html><body>
<form name="search" action="/search"><input type="hidden" name="nope" value="x"></form>
<form name="signIn" method="post" action="/ap/signin">
  <input type="hidden" name="appActionToken" value="token123">
  <input type="hidden" name="metadata1">
  <input type="email" name="email">
  <input type="password" name="password">
</form>
</body></html>
"""

MFA_PAGE = """
<html><body>
<form id="auth-mfa-form" method="post" action="https://www.amazon.com/ap/mfa-submit">
  <input type="hidden" name="mfaState" value="state456">
  <input type="tel" id="auth-mfa-otpcode" name="otpCode">
</form>
</body></html>
"""


def respond(status=200, json_data=None, text="", headers=None):
    if json_data is not None:
        text = json.dumps(json_data)
        headers = {"Content-Type": "application/json", **(headers or {})}
    return status, text, headers or {}


def redirect(location):
    return respond(302, headers={"Location": location})


def register_response(refresh_token="refresh-1"):
    return respond(json_data={"response": {"success": {"tokens": {
        "bearer": {"access_token": "access", "refresh_token": refresh_token, "expires_in": "3600"},
    }}}})


def cookies_response(session_token="session-1"):
    return respond(json_data={"response": {"tokens": {"cookies": {
        ".amazon.es": [
            {"Name": "session-token", "Value": f'"{session_token}"'},
            {"Name": "at-acbes", "Value": "at-cookie"},
        ],
    }}}})


def lists_response():
    return respond(json_data={"listInfoList": [
        {"listId": "todo-id", "listType": "TODO"},
        {"listId": "shop-id", "listType": "SHOP"},
        {"listId": "custom-id", "listType": "CUSTOM", "listName": "Hardware"},
    ]})


def items_response(*items):
    return respond(json_data={"itemInfoList": [
        {"itemId": item_id, "itemName": name, "itemStatus": status, "version": version}
        for item_id, name, status, version in items
    ]})


class FakeAmazon(BaseAdapter):
    """Transport adapter that serves canned responses per (method, url without query).

    Each route holds a list of responses that are used in order; the last one repeats.
    Requests without a route go to the fallback handler, if there is one."""

    def __init__(self):
        super().__init__()
        self.routes = {}
        self.requests = []
        self.fallback = None

    def route(self, method, url, *responses):
        self.routes[(method, url)] = list(responses)

    def send(self, request, **kwargs):
        self.requests.append(request)
        key = (request.method, request.url.split("?")[0])
        if key in self.routes:
            queue = self.routes[key]
            status, text, headers = queue.pop(0) if len(queue) > 1 else queue[0]
        elif self.fallback is not None:
            status, text, headers = self.fallback(request)
        else:
            raise AssertionError(f"Unexpected request: {key}")

        response = requests.Response()
        response.status_code = status
        response._content = text.encode()
        response._content_consumed = True
        response.headers.update(headers)
        response.url = request.url
        response.request = request
        response.encoding = "utf-8"
        return response

    def close(self):
        pass

    def sent(self, method, url):
        return [r for r in self.requests if r.method == method and r.url.split("?")[0] == url]


def query(request):
    return {k: v[0] for k, v in parse_qs(urlparse(request.url).query).items()}


def form(request):
    return {k: v[0] for k, v in parse_qs(request.body, keep_blank_values=True).items()}


def body(request):
    return json.loads(request.body)


class AlexaTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config_path = self._tmp.name
        env = patch.dict(os.environ, {"CONFIG_PATH": self.config_path})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

        self.addCleanup(patch.stopall)
        self.get_totp = patch.object(alexa_api.otp, "get_totp", return_value=12345, create=True).start()
        self.sleep = patch.object(alexa_api.time, "sleep").start()

        self.amazon = FakeAmazon()
        self.session = requests.Session()
        self.session.mount("https://", self.amazon)

    def make_api(self, credential_cache="alexa-credentials.json"):
        return AlexaAPI(
            "amazon.es", "me@example.com", "hunter2", "abcd efgh",
            credential_cache=credential_cache, session=self.session,
        )

    @property
    def credentials_path(self):
        return os.path.join(self.config_path, "alexa-credentials.json")

    def write_credentials(self, **overrides):
        credentials = {
            "device_serial": "CACHEDSERIAL",
            "refresh_token": "cached-refresh",
            "last_login_attempt": None,
            "cookies": [{"domain": ".amazon.es", "name": "session-token", "value": "cached-session"}],
            **overrides,
        }
        with open(self.credentials_path, "w") as file:
            json.dump(credentials, file)

    def read_credentials(self):
        with open(self.credentials_path) as file:
            return json.load(file)

    def route_password_login(self):
        self.amazon.route("GET", SIGNIN, respond(text=SIGNIN_PAGE))
        # Amazon answers the sign-in POST with a 404, even though the page is fine
        self.amazon.route("POST", SIGNIN, respond(404, text=MFA_PAGE))
        self.amazon.route("POST", MFA_SUBMIT, redirect(f"{MAPLANDING}?openid.oa2.authorization_code=AUTHCODE"))
        self.amazon.route("GET", MAPLANDING, respond(404, text="Not found"))
        self.amazon.route("POST", alexa_api.REGISTER_URL, register_response())
        self.amazon.route("POST", alexa_api.TOKEN_URL, cookies_response())


class LoginHelpersTest(unittest.TestCase):
    def _response(self, text="", url="https://www.amazon.com/ap/signin?x=1"):
        response = requests.Response()
        response._content = text.encode()
        response.encoding = "utf-8"
        response.url = url
        return response

    def test_parse_login_page_uses_signin_form_and_its_hidden_inputs(self):
        page = alexa_api.parse_login_page(self._response(SIGNIN_PAGE))

        self.assertEqual(page.method, "POST")
        self.assertEqual(page.action, "https://www.amazon.com/ap/signin")
        self.assertEqual(page.inputs, {"appActionToken": "token123", "metadata1": ""})

    def test_parse_login_page_falls_back_to_first_form(self):
        page = alexa_api.parse_login_page(self._response(MFA_PAGE))

        self.assertEqual(page.action, MFA_SUBMIT)
        self.assertEqual(page.inputs, {"mfaState": "state456"})

    def test_parse_login_page_without_form_raises(self):
        with self.assertRaises(AlexaAuthError):
            alexa_api.parse_login_page(self._response("<html>captcha</html>"))

    def test_find_authorization_code_checks_redirect_history(self):
        redirect_response = self._response(url=f"{MAPLANDING}?openid.oa2.authorization_code=CODE")
        final = self._response(url="https://www.amazon.com/somewhere-else")
        final.history = [redirect_response]

        self.assertEqual(alexa_api.find_authorization_code(final), "CODE")
        self.assertIsNone(alexa_api.find_authorization_code(self._response()))

    def test_totp_code_normalizes_secret_and_zero_pads(self):
        with patch.object(alexa_api.otp, "get_totp", return_value=1234, create=True) as get_totp:
            self.assertEqual(alexa_api.totp_code("abcd efgh ij"), "001234")
        get_totp.assert_called_once_with("ABCDEFGHIJ======")


class PasswordLoginTest(AlexaTestCase):
    def test_full_login_flow(self):
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        api = self.make_api()
        api.login()

        # OAuth sign-in as the Alexa app, with a PKCE challenge
        signin_query = query(self.amazon.sent("GET", SIGNIN)[0])
        self.assertEqual(signin_query["openid.oa2.client_id"], f"device:{api._client_id()}")
        self.assertEqual(signin_query["openid.oa2.code_challenge_method"], "S256")

        # Password is posted along with the form's hidden inputs
        self.assertEqual(form(self.amazon.sent("POST", SIGNIN)[0]), {
            "appActionToken": "token123",
            "metadata1": "",
            "email": "me@example.com",
            "password": "hunter2",
        })

        # Then the OTP code
        self.assertEqual(form(self.amazon.sent("POST", MFA_SUBMIT)[0]), {
            "mfaState": "state456",
            "otpCode": "012345",
            "mfaSubmit": "Submit",
            "rememberDevice": "false",
        })
        self.get_totp.assert_called_once_with("ABCDEFGH")

        # The device registration proves we own the PKCE challenge
        register = body(self.amazon.sent("POST", alexa_api.REGISTER_URL)[0])
        verifier = register["auth_data"]["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(challenge, signin_query["openid.oa2.code_challenge"])
        self.assertEqual(register["auth_data"]["authorization_code"], "AUTHCODE")
        self.assertEqual(register["auth_data"]["client_id"], api._client_id())
        self.assertEqual(register["registration_data"]["device_serial"], api.device_serial)
        self.assertTrue(register["user_context_map"]["frc"])

        # The refresh token is swapped for cookies for our own marketplace
        token_request = form(self.amazon.sent("POST", alexa_api.TOKEN_URL)[0])
        self.assertEqual(token_request["source_token"], "refresh-1")
        self.assertEqual(token_request["requested_token_type"], "auth_cookies")
        self.assertEqual(token_request["domain"], "www.amazon.es")

        # And the lists API gets those cookies, with the quotes stripped
        cookie_header = self.amazon.sent("POST", f"{LISTS}/fetch")[0].headers["Cookie"]
        self.assertIn("session-token=session-1", cookie_header)
        self.assertIn("at-acbes=at-cookie", cookie_header)
        self.assertIn("lc-acbit=es-ES", cookie_header)

    def test_login_saves_private_credentials(self):
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        api = self.make_api()
        api.login()

        credentials = self.read_credentials()
        self.assertEqual(credentials["refresh_token"], "refresh-1")
        self.assertEqual(credentials["device_serial"], api.device_serial)
        self.assertIsNotNone(credentials["last_login_attempt"])
        self.assertIn(
            {"domain": ".amazon.es", "name": "session-token", "value": "session-1"},
            credentials["cookies"],
        )
        self.assertEqual(stat.S_IMODE(os.stat(self.credentials_path).st_mode), 0o600)

    def test_wrong_password_raises_without_registering(self):
        self.amazon.route("GET", SIGNIN, respond(text=SIGNIN_PAGE))
        self.amazon.route("POST", SIGNIN, respond(text=SIGNIN_PAGE))

        with self.assertRaises(AlexaAuthError):
            self.make_api().login()

        self.assertEqual(self.amazon.sent("POST", alexa_api.REGISTER_URL), [])
        # The attempt is still recorded, so a restart loop can't hammer Amazon
        self.assertIsNotNone(self.read_credentials()["last_login_attempt"])

    def test_rejected_otp_raises(self):
        self.route_password_login()
        self.amazon.route("POST", MFA_SUBMIT, respond(text=MFA_PAGE))

        with self.assertRaises(AlexaAuthError):
            self.make_api().login()

        self.assertEqual(self.amazon.sent("POST", alexa_api.REGISTER_URL), [])

    def test_login_without_otp_prompt_skips_otp(self):
        self.route_password_login()
        self.amazon.route("POST", SIGNIN, redirect(f"{MAPLANDING}?openid.oa2.authorization_code=AUTHCODE"))
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        self.make_api().login()

        self.assertEqual(self.amazon.sent("POST", MFA_SUBMIT), [])
        self.get_totp.assert_not_called()

    def test_failed_registration_raises(self):
        self.route_password_login()
        self.amazon.route("POST", alexa_api.REGISTER_URL, respond(400, json_data={"response": {"error": {}}}))

        with self.assertRaises(AlexaAuthError):
            self.make_api().login()

    def test_recent_login_attempt_waits_before_trying_again(self):
        self.write_credentials(refresh_token=None, last_login_attempt=time.time() - 100)
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        self.make_api().login()

        self.sleep.assert_called_once()
        self.assertAlmostEqual(self.sleep.call_args.args[0], AlexaAPI.LOGIN_MIN_INTERVAL - 100, delta=5)

    def test_future_login_attempt_does_not_wait_forever(self):
        self.write_credentials(refresh_token=None, last_login_attempt=time.time() + 10 ** 6)
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        self.make_api().login()

        self.assertLessEqual(self.sleep.call_args.args[0], AlexaAPI.LOGIN_MIN_INTERVAL)

    def test_old_login_attempt_does_not_wait(self):
        self.write_credentials(refresh_token=None, last_login_attempt=time.time() - 10 ** 6)
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        self.make_api().login()

        self.sleep.assert_not_called()


class CachedLoginTest(AlexaTestCase):
    def test_login_with_cached_credentials_skips_password_login(self):
        self.write_credentials()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        api = self.make_api()
        api.login()

        self.assertEqual(self.amazon.sent("GET", SIGNIN), [])
        self.assertEqual(self.amazon.sent("POST", alexa_api.TOKEN_URL), [])
        self.assertIn("session-token=cached-session", self.amazon.requests[0].headers["Cookie"])
        self.assertEqual(api.device_serial, "CACHEDSERIAL")

    def test_rejected_cookies_are_refreshed_with_refresh_token(self):
        self.write_credentials()
        self.amazon.route("POST", f"{LISTS}/fetch", respond(401), lists_response())
        self.amazon.route("POST", alexa_api.TOKEN_URL, cookies_response("fresh-session"))

        self.make_api().login()

        self.assertEqual(self.amazon.sent("GET", SIGNIN), [])
        self.assertEqual(form(self.amazon.sent("POST", alexa_api.TOKEN_URL)[0])["source_token"], "cached-refresh")
        retry = self.amazon.sent("POST", f"{LISTS}/fetch")[1]
        self.assertIn("session-token=fresh-session", retry.headers["Cookie"])
        self.assertNotIn("cached-session", retry.headers["Cookie"])
        self.assertIn(
            {"domain": ".amazon.es", "name": "session-token", "value": "fresh-session"},
            self.read_credentials()["cookies"],
        )

    def test_redirect_to_signin_counts_as_rejected_cookies(self):
        self.write_credentials()
        self.amazon.route("POST", f"{LISTS}/fetch", redirect(f"{SIGNIN}?openid.return_to=x"), lists_response())
        self.amazon.route("GET", SIGNIN, respond(text=SIGNIN_PAGE))
        self.amazon.route("POST", alexa_api.TOKEN_URL, cookies_response())

        self.make_api().login()

        self.assertEqual(len(self.amazon.sent("POST", alexa_api.TOKEN_URL)), 1)
        self.assertEqual(self.amazon.sent("POST", SIGNIN), [])

    def test_revoked_refresh_token_falls_back_to_password_login(self):
        self.write_credentials()
        self.route_password_login()
        self.amazon.route("POST", alexa_api.TOKEN_URL, respond(400, text="revoked"), cookies_response())
        self.amazon.route("POST", f"{LISTS}/fetch", respond(401), lists_response())

        api = self.make_api()
        api.login()

        self.assertEqual(len(self.amazon.sent("POST", SIGNIN)), 1)
        # The same virtual device is reused rather than registering a new one
        register = body(self.amazon.sent("POST", alexa_api.REGISTER_URL)[0])
        self.assertEqual(register["registration_data"]["device_serial"], "CACHEDSERIAL")
        self.assertEqual(self.read_credentials()["refresh_token"], "refresh-1")

    def test_still_rejected_after_refresh_raises(self):
        self.write_credentials()
        api = self.make_api()
        api._load_credentials()
        self.amazon.route("POST", f"{LISTS}/fetch", respond(403))
        self.amazon.route("POST", alexa_api.TOKEN_URL, cookies_response())

        with self.assertRaises(AlexaAuthError):
            api.get_lists()

        self.assertEqual(len(self.amazon.sent("POST", alexa_api.TOKEN_URL)), 1)

    def test_no_credential_cache_logs_in_without_saving(self):
        self.route_password_login()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())

        self.make_api(credential_cache=None).login()

        self.assertFalse(os.path.exists(self.credentials_path))


class ListsTest(AlexaTestCase):
    def setUp(self):
        super().setUp()
        self.write_credentials()
        self.api = self.make_api()
        self.api._load_credentials()
        self.amazon.route("POST", f"{LISTS}/fetch", lists_response())
        self.items_url = f"{LISTS}/shop-id/items/fetch"
        self.amazon.route("POST", self.items_url, items_response(
            ("id-milk-old", "milk", "COMPLETE", 3),
            ("id-milk", "milk", "ACTIVE", 1),
            ("id-eggs", "Eggs", "ACTIVE", 2),
        ))
        self.shopping = self.api.get_shopping_list()

    def test_get_lists(self):
        lists = self.api.get_lists()

        self.assertEqual([(l.identifier, l.type, l.name) for l in lists], [
            ("todo-id", "TODO", "TODO"),
            ("shop-id", "SHOP", "SHOP"),
            ("custom-id", "CUSTOM", "Hardware"),
        ])
        self.assertEqual(self.api.get_list_by_name("Hardware").identifier, "custom-id")
        self.assertIsNone(self.api.get_list_by_name("Nope"))

    def test_lists_requests_send_empty_json_body_and_csrf(self):
        self.session.cookies.set("csrf", "csrf-token", domain=".amazon.es")

        self.api.get_lists()

        request = self.amazon.sent("POST", f"{LISTS}/fetch")[-1]
        self.assertEqual(body(request), {})
        self.assertEqual(request.headers["csrf"], "csrf-token")

    def test_items_are_fetched_lazily(self):
        self.assertEqual(self.amazon.sent("POST", self.items_url), [])

        self.assertEqual(list(self.shopping), [
            AlexaItem("id-milk-old", "milk", True, 3),
            AlexaItem("id-milk", "milk", False, 1),
            AlexaItem("id-eggs", "Eggs", False, 2),
        ])
        self.assertEqual(len(self.shopping), 3)
        self.assertEqual(query(self.amazon.sent("POST", self.items_url)[0]), {"limit": "100"})
        self.assertEqual(len(self.amazon.sent("POST", self.items_url)), 1)

    def test_item_lookup_prefers_active_items(self):
        self.assertEqual(self.shopping.get_item_by_name("milk").identifier, "id-milk")
        self.assertEqual(self.shopping.get_item_by_id("id-milk-old").name, "milk")
        self.assertIn("Eggs", self.shopping)
        self.assertNotIn("eggs", self.shopping)

    def test_add_item(self):
        self.amazon.route("POST", f"{LISTS}/shop-id/items", respond(json_data={}))

        result = self.shopping.add_item("Bread")

        self.assertEqual(body(self.amazon.sent("POST", f"{LISTS}/shop-id/items")[0]), {
            "items": [{"itemType": "KEYWORD", "itemName": "Bread"}],
        })
        self.assertIs(result, self.shopping)
        self.assertEqual(len(self.amazon.sent("POST", self.items_url)), 1)

    def test_remove_item_sends_version(self):
        url = f"{LISTS}/shop-id/items/id-eggs"
        self.amazon.route("DELETE", url, respond(json_data={}))

        self.shopping.remove_item("Eggs")

        request = self.amazon.sent("DELETE", url)[0]
        self.assertEqual(query(request), {"version": "2"})
        self.assertEqual(body(request), {})

    def test_check_uncheck_and_rename(self):
        url = f"{LISTS}/shop-id/items/id-milk"
        self.amazon.route("PUT", url, respond(json_data={}))

        self.shopping.check_item("milk")
        self.shopping.uncheck_item(self.shopping.get_item_by_id("id-milk"))
        self.shopping.rename_item("milk", "Milk")

        updates = [(query(r), body(r)) for r in self.amazon.sent("PUT", url)]
        self.assertEqual(updates, [
            ({"version": "1"}, {"itemAttributesToUpdate": [{"type": "itemStatus", "value": "COMPLETE"}], "itemAttributesToRemove": []}),
            ({"version": "1"}, {"itemAttributesToUpdate": [{"type": "itemStatus", "value": "ACTIVE"}], "itemAttributesToRemove": []}),
            ({"version": "1"}, {"itemAttributesToUpdate": [{"type": "itemName", "value": "Milk"}], "itemAttributesToRemove": []}),
        ])
        # Every change re-fetches the list to pick up the new item versions
        self.assertEqual(len(self.amazon.sent("POST", self.items_url)), 4)

    def test_updates_use_version_from_latest_refresh(self):
        url = f"{LISTS}/shop-id/items/id-eggs"
        self.amazon.route("PUT", url, respond(json_data={}))
        self.amazon.route("POST", self.items_url,
                          items_response(("id-eggs", "Eggs", "ACTIVE", 2)),
                          items_response(("id-eggs", "Eggs", "COMPLETE", 3)))

        self.shopping.check_item("Eggs")
        self.shopping.rename_item(self.shopping.get_item_by_id("id-eggs"), "Big eggs")

        self.assertEqual([query(r)["version"] for r in self.amazon.sent("PUT", url)], ["2", "3"])

    def test_refresh_follows_next_token(self):
        page_one = respond(json_data={"itemInfoList": [
            {"itemId": "id-1", "itemName": "One", "itemStatus": "ACTIVE", "version": 1},
        ], "nextToken": "page-2"})
        page_two = respond(json_data={"itemInfoList": [
            {"itemId": "id-2", "itemName": "Two", "itemStatus": "ACTIVE", "version": 1},
        ], "nextToken": None})
        self.amazon.route("POST", self.items_url, page_one, page_two)

        self.assertEqual([i.name for i in self.shopping.refresh()], ["One", "Two"])

        requests_sent = self.amazon.sent("POST", self.items_url)
        self.assertEqual([body(r) for r in requests_sent], [{}, {"nextToken": "page-2"}])

    def test_unknown_item_raises(self):
        with self.assertRaises(AlexaError):
            self.shopping.remove_item("Caviar")

    def test_server_error_raises(self):
        self.amazon.route("POST", f"{LISTS}/shop-id/items", respond(500, text="oops"))

        with self.assertRaises(AlexaError) as ctx:
            self.shopping.add_item("Bread")

        self.assertNotIsInstance(ctx.exception, AlexaAuthError)

    def test_invalid_json_raises(self):
        self.amazon.route("POST", f"{LISTS}/fetch", respond(text="<html>"))

        with self.assertRaises(AlexaError):
            self.api.get_lists()


if __name__ == "__main__":
    unittest.main()
