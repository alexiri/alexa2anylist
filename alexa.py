#!/usr/bin/env python3

import json
import os
import time
from datetime import datetime
from playwright.sync_api import TimeoutError as PWTimeoutError
import logging

WAIT_TIMEOUT = 30000  # milliseconds

logger = logging.getLogger(__name__)


class AlexaShoppingList:

    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self.is_authenticated = False
        self._setup_browser()

    def _setup_browser(self):
        from cloakbrowser import launch

        headed = os.environ.get("HEADED", "0") == "1"
        self._browser = launch(headless=not headed)
        self._context = self._browser.new_context(viewport={"width": 1366, "height": 768})
        self._page = self._context.new_page()

        self._page.goto(f"https://www.{self.amazon_url}", wait_until="domcontentloaded")
        self._load_cookies()

        if self._page.locator('.nav-action-signin-button').count() == 0:
            self.is_authenticated = True

    # ============================================================
    # Helpers

    def _get_file_location(self):
        return os.path.dirname(os.path.realpath(__file__))

    def _cookie_cache_path(self):
        base = self.cookies_path if self.cookies_path else self._get_file_location()
        return os.path.join(base, "cookies.json")

    def _save_session(self):
        if self.is_authenticated:
            cookies = self._context.cookies()
            with open(self._cookie_cache_path(), "w") as f:
                json.dump(cookies, f)

    def _load_cookies(self):
        path = self._cookie_cache_path()
        if not os.path.exists(path):
            return
        with open(path, "r") as f:
            cookies = json.load(f)
        self._context.add_cookies(cookies)
        self._page.goto(f"https://www.{self.amazon_url}", wait_until="domcontentloaded")
        try:
            self._page.wait_for_selector('#nav-link-accountList', timeout=WAIT_TIMEOUT)
        except PWTimeoutError:
            pass

    def _clear_driver(self):
        self._save_session()
        self._context.close()
        self._browser.close()

    # ============================================================
    # Screenshots (errors only)

    def get_screenshot(self, caption=None):
        out_dir = os.environ.get("SCREENSHOT_PATH", "/screenshots" if os.name != "nt" else "")
        if not out_dir:
            out_dir = os.path.join(self._get_file_location(), "screenshots")
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S.%f")
        if caption:
            caption = caption.replace(" ", "_").replace("/", "_").replace("\\", "_")
            timestamp += f"_{caption}"
        screenshot_path = os.path.join(out_dir, f"{timestamp}_screenshot.png")
        self._page.screenshot(path=screenshot_path, full_page=True)
        with open(os.path.join(out_dir, f"{timestamp}_content.html"), "w", encoding="utf-8") as f:
            f.write(self._page.content())
        print(f"Saved screenshot to {screenshot_path}")
        return screenshot_path

    # ============================================================
    # Authentication

    def _driver_is_on_login_email_page(self):
        if "ap/signin" not in self._page.url:
            return False
        if self._page.locator('input[type="email"]').count() == 0:
            print(" -> No email field found")
            self.get_screenshot("no_email_field")
            return False
        print(" -> Email field found")
        return True

    def _handle_login_email_page(self):
        self._page.fill('input[type="email"]', self.email)
        self._page.press('input[type="email"]', "Enter")
        self._page.wait_for_load_state("domcontentloaded")

    def _driver_is_on_login_password_page(self):
        if self._page.locator('input[type="password"]').count() == 0:
            print(" -> No password field found")
            self.get_screenshot("no_password_field")
            return False
        print(" -> Password field found")
        return True

    def _handle_login_password_page(self):
        print(" -> Password page detected")
        pw_field = self._page.locator('#ap_password')
        if pw_field.count() == 0:
            pw_field = self._page.locator('input[type="password"]')
        pw_field.wait_for(state="visible", timeout=15000)

        # Amazon can move focus for passkey/WebAuthn flows, so always target the
        # field directly and verify the value before submitting.
        pw_field.fill("")

        def _password_len():
            return len(pw_field.input_value() or "")

        pw_field.fill(self.password)
        val_len = _password_len()
        if val_len == 0:
            pw_field.click()
            pw_field.type(self.password, delay=40)
            val_len = _password_len()
        if val_len == 0:
            pw_field.evaluate(
                """(el, value) => {
                    el.focus();
                    el.value = value;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                self.password,
            )
            val_len = _password_len()

        print(f" -> Password length in field: {val_len}")
        if val_len == 0:
            print(" -> Password could not be entered")
            self.get_screenshot("password_not_filled")
            return

        def _submit_outcome_reached(timeout_ms: int = 10000):
            self._page.wait_for_function(
                """() => {
                    const url = window.location.href;
                    const body = document.body;
                    if (!body) return false;
                    const text = (body.innerText || '').toLowerCase();
                    const hasPw = !!document.querySelector('input[type="password"]');
                    const mfa = url.includes('ap/mfa') || !!document.getElementById('auth-mfa-otpcode');
                    const nav = !!document.getElementById('nav-link-accountList') && !url.includes('ap/signin');
                    const puzzle = text.includes('solve this puzzle');
                    const wrongPw = (text.includes('incorrect') || text.includes('incorre')) &&
                        (text.includes('password') || text.includes('contrase'));
                    const cookieWarn = text.includes('please enable cookies');
                    const visibleAlert = [
                        'auth-error-message-box',
                        'auth-warning-message-box',
                        'auth-password-missing-alert',
                        'passkey-error-alert',
                        'auth-cookie-warning-message',
                    ].some((id) => {
                        const el = document.getElementById(id);
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const hiddenByClass = el.classList.contains('aok-hidden');
                        return !hiddenByClass && style.display !== 'none' && style.visibility !== 'hidden';
                    });
                    return mfa || nav || puzzle || wrongPw || cookieWarn || visibleAlert || !hasPw;
                }""",
                timeout=timeout_ms,
            )

        def _attempt_submit(action_name: str, action):
            print(f" -> Submitting password via {action_name}")
            saw_post = False
            try:
                with self._page.expect_response(
                    lambda r: "/ap/signin" in r.url and r.request.method == "POST",
                    timeout=6000,
                ):
                    action()
                saw_post = True
                print(" -> Sign-in POST request detected")
            except PWTimeoutError:
                print(" -> No sign-in POST detected")

            try:
                _submit_outcome_reached(10000)
                return True
            except PWTimeoutError:
                if saw_post:
                    print(" -> POST detected but still no terminal state")
                return False

        submitted = _attempt_submit("Enter key", lambda: pw_field.press("Enter"))

        if not submitted:
            submit = self._page.locator('#signInSubmit')
            if submit.count() == 0:
                submit = self._page.locator('input[type="submit"]').first
            if submit.count() > 0:
                submitted = _attempt_submit("button click", lambda: submit.click())

        if not submitted:
            submitted = _attempt_submit(
                "form requestSubmit",
                lambda: self._page.evaluate(
                    """() => {
                        const form = document.querySelector('form[name="signIn"]');
                        const btn = document.getElementById('signInSubmit');
                        if (form && form.requestSubmit) {
                            form.requestSubmit(btn || undefined);
                            return;
                        }
                        if (btn) {
                            btn.click();
                            return;
                        }
                        if (form) {
                            form.submit();
                        }
                    }"""
                ),
            )

        if not submitted:
            print(" -> No state change after submit")
            self.get_screenshot("password_submit_failed")
            return

        auth_issue = self._page.evaluate(
            """() => {
                const body = document.body;
                if (!body) return '';
                const text = (body.innerText || '').toLowerCase();
                if (text.includes('please enable cookies')) return 'cookie_blocked';
                if ((text.includes('incorrect') || text.includes('incorre')) &&
                    (text.includes('password') || text.includes('contrase'))) {
                    return 'wrong_password';
                }
                if (text.includes('enter your password')) return 'missing_password';
                return '';
            }"""
        )

        if auth_issue == "wrong_password":
            print(" -> ERROR: Amazon says the password is incorrect. Check config.json.")
            self.get_screenshot("wrong_password")
            return
        if auth_issue == "cookie_blocked":
            print(" -> ERROR: Amazon rejected sign-in because cookies are blocked.")
            self.get_screenshot("cookies_blocked")
            return
        if auth_issue == "missing_password":
            print(" -> ERROR: Amazon still reports the password is missing after submit.")
            self.get_screenshot("password_missing_after_submit")
            return

        if self.login_requires_puzzle():
            self.wait_for_puzzle_completion()

        print(" -> Password submission flow appears complete")

    def login_requires_puzzle(self):
        return self._page.locator("text=Solve this puzzle to protect your account").count() > 0

    def wait_for_puzzle_completion(self):
        print(" -> CAPTCHA puzzle detected. Please solve it in the browser window.")
        print(" -> Waiting up to 5 minutes for you to complete the puzzle...")
        try:
            self._page.wait_for_function(
                "() => !document.body.innerText.includes('Solve this puzzle to protect your account')",
                timeout=300000
            )
            print(" -> Puzzle appears solved, continuing...")
            self._page.wait_for_load_state("domcontentloaded")
        except PWTimeoutError:
            print(" -> Timed out waiting for puzzle completion")
            raise

    def login_requires_mfa(self):
        return "ap/mfa" in self._page.url

    def submit_mfa(self, code: str):
        code_str = str(code).strip()
        if code_str.isdigit() and len(code_str) < 6:
            code_str = code_str.zfill(6)

        print(f" -> Submitting MFA code: {code_str}")
        self._page.fill("#auth-mfa-otpcode", code_str)
        remember = self._page.locator("#auth-mfa-remember-device")
        if remember.count() > 0:
            remember.click()
        self._page.locator('input[type="submit"]').click()
        time.sleep(5)
        if not self.login_requires_mfa():
            self._login_successful()

    def login(self, email: str, password: str):
        # Navigate to homepage only if not already there
        home = f"https://www.{self.amazon_url}"
        if not self._page.url.startswith(home):
            self._page.goto(home, wait_until="domcontentloaded")
        self._page.wait_for_selector('#nav-link-accountList', timeout=WAIT_TIMEOUT)

        signin_url = self._page.get_attribute('a[data-nav-role="signin"]', "href")
        if not signin_url:
            print(" -> Could not find sign-in link on homepage")
            self.get_screenshot("no_signin_link")
            return
        print(f" -> Signin URL: {signin_url}")
        self._page.goto(signin_url, wait_until="domcontentloaded")

        # If cookies were valid, Amazon may skip sign-in entirely
        if "ap/signin" not in self._page.url and "ap/mfa" not in self._page.url:
            print(" -> Already authenticated via cookies")
            self._login_successful()
            return

        self._page.wait_for_selector('input[type="email"]', timeout=WAIT_TIMEOUT)

        self.email = email
        self.password = password

        if self._driver_is_on_login_email_page():
            self._handle_login_email_page()
        else:
            print(" -> Not on login email page")
            self.get_screenshot("not_on_email_page")
            return

        if self._driver_is_on_login_password_page():
            self._handle_login_password_page()
        else:
            print(" -> Not on login password page")
            self.get_screenshot("not_on_password_page")
            return

        self.email = ""
        self.password = ""

        self._page.wait_for_load_state("domcontentloaded")
        if not self.login_requires_mfa():
            if "ap/signin" not in self._page.url and "ap/mfa" not in self._page.url:
                self._login_successful()
            else:
                print(" -> Login failed")
                self.get_screenshot("login_failed")

    def _login_successful(self):
        self.is_authenticated = True
        self._save_session()

    def requires_login(self):
        if "ap/signin" in self._page.url:
            return True
        if self._page.locator('.nav-action-signin-button').count() > 0:
            return True
        if not self.is_authenticated:
            return True
        return False

    # ============================================================
    # Alexa lists

    def _ensure_on_alexa_list(self, refresh: bool = False):
        list_url = f"https://www.{self.amazon_url}/alexaquantum/sp/alexaShoppingList?ref=nav_asl"
        if self._page.url != list_url:
            self._page.goto(list_url, wait_until="domcontentloaded")
            try:
                self._page.wait_for_selector('.list-header', timeout=WAIT_TIMEOUT)
            except PWTimeoutError:
                self.get_screenshot("alexa_list_load_failed")
                raise
        elif refresh:
            self._page.reload(wait_until="domcontentloaded")
            self._page.wait_for_selector('.list-header', timeout=WAIT_TIMEOUT)

    def get_alexa_list(self, refresh: bool = True):
        self._ensure_on_alexa_list(refresh)
        time.sleep(5)

        found = []
        last_text = None
        while True:
            items = self._page.locator('.virtual-list .item-title').all()
            texts = [el.inner_text() for el in items]
            if texts:
                print(f"Found {len(texts)} items: from {texts[0]} to {texts[-1]}")
            for t in texts:
                if t not in found:
                    found.append(t)
            if not texts or texts[-1] == last_text:
                break
            last_text = texts[-1]
            print("Scrolling...")
            items[-1].scroll_into_view_if_needed()
            time.sleep(1)

        if not refresh:
            # Scroll back to top
            first_text = None
            while True:
                items = self._page.locator('.virtual-list .item-title').all()
                if not items:
                    return []
                t = items[0].inner_text()
                if t == first_text:
                    break
                first_text = t
                self._page.mouse.wheel(0, -1000)
                time.sleep(0.5)

        return found

    def _get_alexa_list_item_element(self, item: str):
        self._ensure_on_alexa_list(False)
        time.sleep(5)

        last_text = None
        while True:
            containers = self._page.locator('.virtual-list .inner').all()
            for container in containers:
                if container.locator('.item-title').inner_text() == item:
                    return container
            if not containers:
                return None
            texts = [c.locator('.item-title').inner_text() for c in containers]
            if texts[-1] == last_text:
                return None
            last_text = texts[-1]
            containers[-1].scroll_into_view_if_needed()
            time.sleep(1)

    def add_alexa_list_item(self, item: str):
        if self._get_alexa_list_item_element(item) is not None:
            return

        self._page.locator('.list-header .add-symbol').click()
        self._page.locator('.list-header .input-box input').fill(item)
        self._page.locator('.list-header .add-to-list button').click()
        self._page.locator('.list-header .cancel-input').click()
        time.sleep(1)

        return self.get_alexa_list(False)

    def update_alexa_list_item(self, old: str, new: str):
        element = self._get_alexa_list_item_element(old)
        if element is None:
            return

        element.locator('.item-actions-1 button').click()
        field = element.locator('.input-box input')
        field.fill(new)
        element.locator('.item-actions-2 button').click()
        time.sleep(1)

        return self.get_alexa_list(False)

    def remove_alexa_list_item(self, item: str):
        element = self._get_alexa_list_item_element(item)
        if element is None:
            return

        element.locator('.item-actions-2 button').click()
        time.sleep(1)

        return self.get_alexa_list(False)
