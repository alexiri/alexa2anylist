"""Alexa shopping list client using Amazon's (unofficial) lists API.

This is a trimmed-down port of the login and to-do list code in
aioamazondevices (https://github.com/chemelli74/aioamazondevices), the library
behind Home Assistant's alexa_devices integration.

We log in the way the Alexa iOS app does: an OAuth sign-in with password and
OTP code, after which we register a virtual device to get a long-lived refresh
token. That token can mint fresh website cookies whenever the old ones stop
working, so the password login should only be needed once.
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import onetimepass as otp
import requests

# Identify ourselves as the Alexa iOS app, with the same values as aioamazondevices
APP_NAME = "alexa2anylist"
APP_BUNDLE_ID = "com.amazon.echo"
APP_ID = "MAPiOSLib/6.0/ToHideRetailLink"
APP_VERSION = "2.2.663733.0"
DEVICE_TYPE = "A2IVLV5VM2W81"
DEVICE_SOFTWARE_VERSION = "35602678"
CLIENT_OS = "18.5"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0"
)

# The app always signs in through amazon.com, whatever the user's marketplace
SIGNIN_URL = "https://www.amazon.com/ap/signin"
REGISTER_URL = "https://api.amazon.com/auth/register"
TOKEN_URL = "https://api.amazon.com/auth/token"
LISTS_PATH = "alexashoppinglists/api/v2/lists"
REQUEST_TIMEOUT = 30

LANGUAGES = {
    "com": "en-US",
    "ca": "en-CA",
    "co.uk": "en-GB",
    "com.au": "en-AU",
    "in": "en-IN",
    "de": "de-DE",
    "es": "es-ES",
    "fr": "fr-FR",
    "it": "it-IT",
    "nl": "nl-NL",
    "com.br": "pt-BR",
    "com.mx": "es-MX",
    "co.jp": "ja-JP",
}

STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETE = "COMPLETE"


class AlexaError(Exception):
    pass


class AlexaAuthError(AlexaError):
    pass


# ============================================================
# Login page parsing


@dataclass
class _LoginPage:
    method: str
    action: str
    inputs: dict


class _LoginPageParser(HTMLParser):
    """Collects every form's action, method and hidden inputs."""

    def __init__(self):
        super().__init__()
        self.forms = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self._form = {
                "name": attrs.get("name"),
                "method": attrs.get("method") or "get",
                "action": attrs.get("action"),
                "inputs": {},
            }
            self.forms.append(self._form)
        elif tag == "input":
            if self._form is not None and attrs.get("type") == "hidden" and attrs.get("name"):
                self._form["inputs"][attrs["name"]] = attrs.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def parse_login_page(response):
    parser = _LoginPageParser()
    parser.feed(response.text)
    form = next((f for f in parser.forms if f["name"] == "signIn"), None)
    if form is None and parser.forms:
        form = parser.forms[0]
    if form is None or not form["action"]:
        raise AlexaAuthError(f"No sign-in form found on Amazon page {response.url}")
    return _LoginPage(
        method=form["method"].upper(),
        action=urljoin(response.url, form["action"]),
        inputs=form["inputs"],
    )


def find_authorization_code(response):
    for r in [*response.history, response]:
        codes = parse_qs(urlparse(r.url).query).get("openid.oa2.authorization_code")
        if codes:
            return codes[0]
    return None


def totp_code(secret):
    secret = secret.replace(" ", "").upper()
    secret += "=" * (-len(secret) % 8)
    return str(otp.get_totp(secret)).zfill(6)


# ============================================================
# API


class AlexaAPI:
    # Amazon puts up captchas (or worse) when it sees too many password logins
    LOGIN_MIN_INTERVAL = 600
    # The most the API returns in one go
    ITEM_PAGE_SIZE = 100

    def __init__(self, amazon_url, email, password, mfa_secret, credential_cache=None, session=None):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self.domain = amazon_url.removeprefix("https://").removeprefix("www.").removeprefix("amazon.")
        self.site = f"https://www.amazon.{self.domain}"
        self.language = LANGUAGES.get(self.domain, "en-US")
        self.email = email
        self.password = password
        self.mfa_secret = mfa_secret
        self.credential_cache = credential_cache
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": self.language})
        self.device_serial = uuid.uuid4().hex.upper()
        self.refresh_token = None
        self.last_login_attempt = None

    def login(self):
        if self._load_credentials():
            try:
                self.get_lists()
                return
            except AlexaAuthError:
                self.log.warning("Cached Alexa credentials were rejected, logging in again")
        self._login_with_password()
        self.get_lists()

    # ============================================================
    # Credential cache

    def _credentials_path(self):
        if not self.credential_cache:
            return None
        config_path = os.environ.get("CONFIG_PATH", os.path.dirname(os.path.realpath(__file__)))
        return os.path.join(config_path, self.credential_cache)

    def _load_credentials(self):
        path = self._credentials_path()
        if path is None or not os.path.exists(path):
            return False

        self.log.info("Loading Alexa credential cache from %s", path)
        with open(path, "r") as file:
            credentials = json.load(file)
        self.device_serial = credentials.get("device_serial", self.device_serial)
        self.refresh_token = credentials.get("refresh_token")
        self.last_login_attempt = credentials.get("last_login_attempt")
        for cookie in credentials.get("cookies", []):
            self.session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
        return self.refresh_token is not None

    def _save_credentials(self):
        path = self._credentials_path()
        if path is None:
            return

        credentials = {
            "device_serial": self.device_serial,
            "refresh_token": self.refresh_token,
            "last_login_attempt": self.last_login_attempt,
            "cookies": [
                {"domain": c.domain, "name": c.name, "value": c.value}
                for c in self.session.cookies
            ],
        }
        # Write atomically so a crash mid-write can't leave a corrupt file behind
        tmp_path = f"{path}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(credentials, file)
        os.replace(tmp_path, path)

    # ============================================================
    # Password login

    def _wait_for_login_slot(self):
        if self.last_login_attempt is not None:
            # Clamp so a clock that jumped backwards can't make us wait forever
            wait = min(self.LOGIN_MIN_INTERVAL, self.last_login_attempt + self.LOGIN_MIN_INTERVAL - time.time())
            if wait > 0:
                self.log.warning("Last Alexa login attempt was recent, waiting %.0fs before trying again", wait)
                time.sleep(wait)

        # Record the attempt before making it, in case we die mid-login
        self.last_login_attempt = time.time()
        self._save_credentials()

    def _login_with_password(self):
        self._wait_for_login_slot()
        self.log.info("Logging in to Amazon with password")

        self.session.cookies.clear()
        frc = base64.b64encode(secrets.token_bytes(313)).decode().rstrip("=")
        map_md = json.dumps({
            "device_user_dictionary": [],
            "device_registration_data": {"software_version": DEVICE_SOFTWARE_VERSION},
            "app_identifier": {"app_version": APP_VERSION, "bundle_id": APP_BUNDLE_ID},
        }, separators=(",", ":"))
        for name, value in [
            ("amzn-app-id", APP_ID),
            ("frc", frc),
            ("map-md", base64.b64encode(map_md.encode()).decode().rstrip("=")),
        ]:
            self.session.cookies.set(name, value, domain=".amazon.com")

        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
        response = self.session.get(SIGNIN_URL, params=self._oauth_params(code_verifier), timeout=REQUEST_TIMEOUT)
        page = parse_login_page(response)
        response = self._submit(page, {"email": self.email, "password": self.password})

        if find_authorization_code(response) is None:
            if "auth-mfa-otpcode" not in response.text:
                raise self._login_failed(response)
            self.log.info("Submitting OTP code")
            response = self._submit(parse_login_page(response), {
                "otpCode": totp_code(self.mfa_secret),
                "mfaSubmit": "Submit",
                "rememberDevice": "false",
            })

        authorization_code = find_authorization_code(response)
        if authorization_code is None:
            raise self._login_failed(response)

        self._register_device(authorization_code, code_verifier, frc)
        self._refresh_cookies()
        self.log.info("Logged in to Amazon")

    def _login_failed(self, response):
        self.log.debug("Unexpected login page %s: %r", response.url, response.text[:500])
        return AlexaAuthError(
            f"Amazon login did not complete (ended on {urlparse(response.url).path}); "
            "check the credentials and OTP secret, or whether Amazon wants a captcha solved"
        )

    def _oauth_params(self, code_verifier):
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode()).digest()
        ).rstrip(b"=").decode()
        return {
            "openid.return_to": "https://www.amazon.com/ap/maplanding",
            "openid.oa2.code_challenge_method": "S256",
            "openid.assoc_handle": "amzn_dp_project_dee_ios",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "pageId": "amzn_dp_project_dee_ios",
            "accountStatusPolicy": "P1",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.mode": "checkid_setup",
            "openid.ns.oa2": "http://www.amazon.com/ap/ext/oauth/2",
            "openid.oa2.client_id": f"device:{self._client_id()}",
            "language": "en-US",
            "openid.ns.pape": "http://specs.openid.net/extensions/pape/1.0",
            "openid.oa2.code_challenge": code_challenge,
            "openid.oa2.scope": "device_auth_access",
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.pape.max_auth_age": "0",
            "openid.oa2.response_type": "code",
        }

    def _client_id(self):
        return f"{self.device_serial}#{DEVICE_TYPE}".encode().hex()

    def _submit(self, page, fields):
        # Amazon's sign-in pages answer 404 after redirecting, but the content is still good
        return self.session.request(
            page.method, page.action, data={**page.inputs, **fields}, timeout=REQUEST_TIMEOUT
        )

    def _register_device(self, authorization_code, code_verifier, frc):
        response = self.session.post(REGISTER_URL, timeout=REQUEST_TIMEOUT, json={
            "requested_extensions": ["device_info", "customer_info"],
            "cookies": {"website_cookies": [], "domain": ".amazon.com"},
            "registration_data": {
                "domain": "Device",
                "app_version": APP_VERSION,
                "device_type": DEVICE_TYPE,
                "device_name": f"%FIRST_NAME%'s%DUPE_STRATEGY_1ST%{APP_NAME}",
                "os_version": CLIENT_OS,
                "device_serial": self.device_serial,
                "device_model": "iPhone",
                "app_name": APP_NAME,
                "software_version": DEVICE_SOFTWARE_VERSION,
            },
            "auth_data": {
                "use_global_authentication": "true",
                "client_id": self._client_id(),
                "authorization_code": authorization_code,
                "code_verifier": code_verifier,
                "code_algorithm": "SHA-256",
                "client_domain": "DeviceLegacy",
            },
            "user_context_map": {"frc": frc},
            "requested_token_type": ["bearer", "mac_dms", "website_cookies", "store_authentication_cookie"],
        })
        if response.status_code != 200:
            raise AlexaAuthError(f"Amazon device registration failed: HTTP {response.status_code} {response.text[:200]!r}")
        self.refresh_token = response.json()["response"]["success"]["tokens"]["bearer"]["refresh_token"]
        self.log.info("Registered virtual Alexa device %s", self.device_serial)

    def _refresh_cookies(self):
        """Swaps the refresh token for a fresh set of cookies for our Amazon site."""
        if not self.refresh_token:
            raise AlexaAuthError("No Alexa refresh token, a password login is needed")

        self.log.info("Refreshing Amazon cookies for %s", self.site)
        response = self.session.post(TOKEN_URL, timeout=REQUEST_TIMEOUT, data={
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "di.sdk.version": "6.12.4",
            "source_token": self.refresh_token,
            "package_name": APP_BUNDLE_ID,
            "di.hw.version": "iPhone",
            "platform": "iOS",
            "requested_token_type": "auth_cookies",
            "source_token_type": "refresh_token",
            "di.os.name": "iOS",
            "di.os.version": CLIENT_OS,
            "current_version": "6.12.4",
            "previous_version": "6.12.4",
            "domain": f"www.amazon.{self.domain}",
        })
        if response.status_code != 200:
            raise AlexaAuthError(f"Amazon cookie refresh failed: HTTP {response.status_code} {response.text[:200]!r}")

        self.session.cookies.clear()
        cookies = response.json()["response"]["tokens"]["cookies"]
        for cookie_domain, entries in cookies.items():
            for cookie in entries:
                self.session.cookies.set(cookie["Name"], cookie["Value"].replace('"', ""), domain=cookie_domain)
        self.session.cookies.set("lc-acbit", self.language, domain=f".amazon.{self.domain}")
        self._save_credentials()

    # ============================================================
    # Lists API

    def _send(self, method, url, params, body):
        headers = {}
        csrf = next((c.value for c in self.session.cookies if c.name == "csrf"), None)
        if csrf:
            headers["csrf"] = csrf
        # The API rejects requests without a body, even when it has nothing to say
        return self.session.request(
            method, url, params=params, json=body or {}, headers=headers, timeout=REQUEST_TIMEOUT
        )

    @staticmethod
    def _is_auth_failure(response):
        return response.status_code in (401, 403, 407) or "/ap/signin" in urlparse(response.url).path

    def _request(self, method, path, params=None, body=None):
        url = f"{self.site}/{LISTS_PATH}/{path}"
        response = self._send(method, url, params, body)
        if self._is_auth_failure(response):
            self.log.info("Amazon rejected our cookies (HTTP %s)", response.status_code)
            self._refresh_cookies()
            response = self._send(method, url, params, body)
            if self._is_auth_failure(response):
                raise AlexaAuthError(f"{method} {path} rejected even with fresh cookies: HTTP {response.status_code}")

        if not response.ok:
            raise AlexaError(f"{method} {path} failed: HTTP {response.status_code} {response.text[:200]!r}")
        return response

    def _request_json(self, method, path, params=None, body=None):
        response = self._request(method, path, params, body)
        try:
            return response.json() or {}
        except ValueError as e:
            raise AlexaError(f"{method} {path} returned invalid JSON: {response.text[:200]!r}") from e

    def get_lists(self):
        data = self._request_json("POST", "fetch")
        return [AlexaList(self, info) for info in data.get("listInfoList", [])]

    def get_shopping_list(self):
        return next((lst for lst in self.get_lists() if lst.type == "SHOP"), None)

    def get_list_by_name(self, name):
        return next((lst for lst in self.get_lists() if lst.name == name), None)


@dataclass
class AlexaItem:
    identifier: str
    name: str
    checked: bool
    version: int

    @classmethod
    def from_api(cls, info):
        return cls(
            identifier=info["itemId"],
            name=info["itemName"],
            checked=info["itemStatus"] == STATUS_COMPLETE,
            version=info["version"],
        )


class AlexaList:
    """An Alexa list. Items are fetched on first use; mutations re-fetch them,
    since each change bumps the item version the API requires for the next one."""

    def __init__(self, api, list_info):
        self._api = api
        self.identifier = list_info["listId"]
        self.type = list_info["listType"]
        # Only custom lists have a name, the built-in ones are named by type
        self.name = list_info.get("listName") or self.type
        self._items = None

    def __repr__(self) -> str:
        return f"AlexaList('{self.name}', {self.identifier})"

    @property
    def items(self):
        if self._items is None:
            self.refresh()
        return self._items

    def __iter__(self):
        yield from self.items

    def __len__(self):
        return len(self.items)

    def __contains__(self, name):
        return self.get_item_by_name(name) is not None

    def refresh(self):
        # Completed items stay on the list, so it can easily outgrow a single page
        items = []
        next_token = None
        while True:
            data = self._api._request_json(
                "POST",
                f"{self.identifier}/items/fetch",
                params={"limit": AlexaAPI.ITEM_PAGE_SIZE},
                body={"nextToken": next_token} if next_token else None,
            )
            items.extend(AlexaItem.from_api(info) for info in data.get("itemInfoList", []))
            next_token = data.get("nextToken")
            if not next_token:
                break
        self._items = items
        return self

    def get_item_by_id(self, identifier):
        return next((i for i in self.items if i.identifier == identifier), None)

    def get_item_by_name(self, name):
        # Alexa keeps completed items around, so prefer an active item with the same name
        matches = sorted((i for i in self.items if i.name == name), key=lambda i: i.checked)
        return matches[0] if matches else None

    def _resolve(self, item):
        if isinstance(item, AlexaItem):
            return item
        found = self.get_item_by_name(item)
        if found is None:
            raise AlexaError(f"No item named {item!r} in {self}")
        return found

    def add_item(self, name):
        self._api._request("POST", f"{self.identifier}/items", body={
            "items": [{"itemType": "KEYWORD", "itemName": name}],
        })
        return self.refresh()

    def remove_item(self, item):
        item = self._resolve(item)
        self._api._request("DELETE", f"{self.identifier}/items/{item.identifier}", params={"version": item.version})
        return self.refresh()

    def _update_item(self, item, attribute, value):
        item = self._resolve(item)
        self._api._request(
            "PUT",
            f"{self.identifier}/items/{item.identifier}",
            params={"version": item.version},
            body={"itemAttributesToUpdate": [{"type": attribute, "value": value}], "itemAttributesToRemove": []},
        )
        return self.refresh()

    def rename_item(self, item, new_name):
        return self._update_item(item, "itemName", new_name)

    def check_item(self, item):
        return self._update_item(item, "itemStatus", STATUS_COMPLETE)

    def uncheck_item(self, item):
        return self._update_item(item, "itemStatus", STATUS_ACTIVE)


class AlexaShoppingList:
    """The name-based view of the Alexa shopping list that the Synchronizer uses.

    Only active items count; completed ones are treated as gone. Items removed
    here are deleted outright rather than completed, as Alexa never cleans up
    completed items by itself."""

    def __init__(self, api):
        self._api = api
        self._list = None

    def _shopping_list(self):
        if self._list is None:
            self._list = self._api.get_shopping_list()
            if self._list is None:
                raise AlexaError("Alexa account has no shopping list")
        return self._list

    def _active_item(self, name):
        item = self._shopping_list().get_item_by_name(name)
        return item if item is not None and not item.checked else None

    def _names(self):
        return [item.name for item in self._shopping_list() if not item.checked]

    def get_alexa_list(self, refresh=True):
        if refresh:
            self._shopping_list().refresh()
        return self._names()

    def add_alexa_list_item(self, name):
        item = self._shopping_list().get_item_by_name(name)
        if item is None:
            self._shopping_list().add_item(name)
        elif item.checked:
            self._shopping_list().uncheck_item(item)
        return self._names()

    def update_alexa_list_item(self, old, new):
        item = self._active_item(old)
        if item is None:
            return None
        self._shopping_list().rename_item(item, new)
        return self._names()

    def remove_alexa_list_item(self, name):
        item = self._active_item(name)
        if item is None:
            return None
        self._shopping_list().remove_item(item)
        return self._names()
