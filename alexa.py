#!/usr/bin/env python3

from selenium import webdriver
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.actions.wheel_input import ScrollOrigin
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import ElementClickInterceptedException, StaleElementReferenceException
import time
import json
import os
from datetime import datetime
import logging

WAIT_TIMEOUT=30

class AlexaShoppingList:

    LOGIN_SELECTOR    = (By.CSS_SELECTOR, 'input[type="email"]')
    PASSWORD_SELECTOR = (By.CSS_SELECTOR, 'input[type="password"]')
    SUBMIT_SELECTOR   = (By.CSS_SELECTOR, 'input[type="submit"]')

    def __init__(self, amazon_url: str = "amazon.co.uk", cookies_path: str = ""):
        self.amazon_url = amazon_url
        self.cookies_path = cookies_path
        self._setup_driver()


    # def __del__(self):
    #     self._clear_driver()

    # ============================================================
    # Helpers


    def _get_file_location(self):
        return os.path.dirname(os.path.realpath(__file__))

    # ============================================================
    # Selenium


    def _setup_driver(self):
        user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("window-size=1366,768")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"--user-agent={user_agent}")

        chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

        driver_path = os.environ.get("CHROME_DRIVER", "")
        if driver_path != "":
            service = webdriver.ChromeService(executable_path=driver_path)
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            self.driver = webdriver.Chrome(options=chrome_options)

        self.is_authenticated = False
        self._selenium_get("https://www."+self.amazon_url, (By.TAG_NAME, 'body'))
        self._load_cookies()

        if len(self.driver.find_elements(By.ID, 'nav-backup-backup')) > 0:
            self.driver.find_element(By.CLASS_NAME, "nav-bb-right").find_element(By.LINK_TEXT, "Your Account").click()
            time.sleep(5)

        if len(self.driver.find_elements(By.CLASS_NAME, 'nav-action-signin-button')) > 0:
            self.driver.find_element(By.ID, 'nav-link-accountList').click()
            self._selenium_wait_element(AlexaShoppingList.LOGIN_SELECTOR)
        else:
            self.is_authenticated = True


    def get_screenshot(self, caption = None):
        # We just need this for debugging purposes
        if hasattr(self, "driver") and os.path.exists("/out"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S.%f")
            if caption is not None:
                caption = caption.replace(" ", "_").replace("/", "_").replace("\\", "_")
                timestamp += f"_{caption}"
            screenshot_path = f"/out/{timestamp}_screenshot.png"
            self.driver.get_screenshot_as_file(screenshot_path)

            html = self.driver.execute_script("return document.body.innerHTML;")
            with open(f"/out/{timestamp}_content.html","w") as f:
                f.write(html)

            print(f"Saved screenshot to {screenshot_path}")
            return screenshot_path
        return None

    def _clear_driver(self):
        if hasattr(self, "driver"):
            self._save_session()
            self.driver.close()


    def _selenium_wait_element(self, element: tuple):
        try:
            WebDriverWait(self.driver, WAIT_TIMEOUT).until(EC.presence_of_element_located(element))
        except Exception as e:
            print(f"Error occurred while waiting for element {element}: {e}")
            self.get_screenshot()
            raise e

    def _selenium_wait_page_ready(self):
        try:
            WebDriverWait(self.driver, WAIT_TIMEOUT).until(
                lambda d: d.execute_script('return document.readyState') == 'complete'
            )
        except Exception as e:
            print(f"Error occurred while waiting for page to be ready: {e}")
            self.get_screenshot()
            raise e

    def _selenium_get(self, url: str, wait_for_element: tuple=None, wait_for_page_load: bool=False):
        self.driver.get(url)

        if wait_for_element != None:
            self._selenium_wait_element(wait_for_element)

        if wait_for_page_load:
            self._selenium_wait_page_ready()


    def _cookie_cache_path(self):
        if self.cookies_path != "":
            return os.path.join(self.cookies_path, "cookies.json")
        return os.path.join(self._get_file_location(), "cookies.json")


    def _save_session(self):
        return
        if self.is_authenticated:
            with open(self._cookie_cache_path(), 'w') as file:
                json.dump(self.driver.get_cookies(), file)


    def _load_cookies(self):
        if os.path.exists(self._cookie_cache_path()):

            with open(self._cookie_cache_path(), 'r') as file:
                cookies = json.load(file)

            for cookie in cookies:
                self.driver.add_cookie(cookie)

            self.driver.refresh()
            self._selenium_wait_element((By.ID, 'nav-link-accountList'))


    # ============================================================
    # Authentication


    def _driver_is_on_login_email_page(self):
        if 'ap/signin' not in self.driver.current_url:
            return False

        if len(self.driver.find_elements(*AlexaShoppingList.LOGIN_SELECTOR)) == 0:
            print(" -> No email field found")
            self.get_screenshot("no_email_field")
            return False

        print(" -> Email field found")
        return True


    def _handle_login_email_page(self):
        self.driver.find_element(*AlexaShoppingList.LOGIN_SELECTOR).send_keys(self.email)
        self.get_screenshot()
        self.driver.find_element(*AlexaShoppingList.LOGIN_SELECTOR).send_keys(Keys.RETURN)
        self._selenium_wait_page_ready()


    def _driver_is_on_login_password_page(self):
        if len(self.driver.find_elements(*AlexaShoppingList.PASSWORD_SELECTOR)) == 0:
            print(" -> No password field found")
            self.get_screenshot("no_password_field")
            return False

        print(" -> Password field found")
        return True


    def _handle_login_password_page(self):
        print(" -> Password page detected")
        # self.get_screenshot("password_page_before")

        WebDriverWait(self.driver, 15).until(
            EC.element_to_be_clickable(AlexaShoppingList.PASSWORD_SELECTOR)
        )
        password_field = self.driver.find_element(*AlexaShoppingList.PASSWORD_SELECTOR)

        # 1. Try genuine typing first (Amazon sometimes checks typing events)
        password_field.clear()
        typed = False
        try:
            for ch in self.password:
                password_field.send_keys(ch)
                time.sleep(0.04)  # slight human-like delay
            if password_field.get_attribute("value"):
                typed = True
                print(" -> send_keys typing succeeded")
        except Exception as e:
            print(f" -> send_keys failed: {e}")

        # 2. Fallback to JS injection with proper events if typing failed
        if not typed or password_field.get_attribute("value") == "":
            print(" -> Falling back to JS value injection")
            self.driver.execute_script("""
                const el = arguments[0];
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.value = arguments[1];
                el.dispatchEvent(new Event('input', {bubbles:true}));
                el.dispatchEvent(new Event('change', {bubbles:true}));
            """, password_field, self.password)

        val_len = len(password_field.get_attribute("value") or "")
        print(f" -> Password length in field: {val_len}")
        self.get_screenshot("password_filled")

        # 3. Locate submit (your generic selector retained)
        try:
            submit_btn = self.driver.find_element(*AlexaShoppingList.SUBMIT_SELECTOR)
        except Exception as e:
            print(f" -> Cannot find submit button: {e}")
            self.get_screenshot("no_submit_button")
            return

        print(f" -> Submit button attrs: id={submit_btn.get_attribute('id')} "
              f"name={submit_btn.get_attribute('name')} value={submit_btn.get_attribute('value')} "
              f"disabled={submit_btn.get_attribute('disabled')} aria-disabled={submit_btn.get_attribute('aria-disabled')}")

        def post_submit_condition(d):
            # Success if password field gone OR MFA present OR URL changed away from signin
            still_on_signin = 'ap/signin' in d.current_url
            has_password = len(d.find_elements(*AlexaShoppingList.PASSWORD_SELECTOR)) > 0
            mfa = 'ap/mfa' in d.current_url or len(d.find_elements(By.ID, 'auth-mfa-otpcode')) > 0
            nav_account = len(d.find_elements(By.ID, 'nav-link-accountList')) > 0 and not still_on_signin
            return mfa or nav_account or (still_on_signin is False and has_password is False) or (has_password is False)

        attempts = [
            ("js_click", lambda: self.driver.execute_script("arguments[0].click();", submit_btn)),
            ("form_submit", lambda: self.driver.execute_script("arguments[0].closest('form').submit();", submit_btn)),
            ("native_click", lambda: submit_btn.click()),
            ("scroll_then_click", lambda: (self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit_btn), submit_btn.click())),
            ("action_chains_click", lambda: ActionChains(self.driver).move_to_element(submit_btn).click().perform()),
        ]

        success = False
        for label, action in attempts:
            print(f" -> Attempting submit via: {label}")
            try:
                action()
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                print(f"    {label} exception: {e}")
                try:
                    submit_btn = self.driver.find_element(*AlexaShoppingList.SUBMIT_SELECTOR)
                except Exception:
                    pass
            except Exception as e:
                print(f"    {label} generic exception: {e}")

            self.get_screenshot(f"after_{label}")
            try:
                WebDriverWait(self.driver, 8).until(post_submit_condition)
                success = True
                print(f" -> Submission recognized after {label}")
                break
            except Exception:
                print(f" -> No state change after {label}")
                # Re-acquire button if still present
                try:
                    submit_btn = self.driver.find_element(*AlexaShoppingList.SUBMIT_SELECTOR)
                except Exception:
                    pass

        if not success:
            print(" -> All submit strategies failed; still on password page")
            self.get_screenshot("password_submit_failed")
        else:
            # Give a short grace period for any redirect chain
            time.sleep(2)
            self.get_screenshot("post_password_flow")
            print(" -> Password submission flow appears complete")


    def login_requires_mfa(self):
        if 'ap/mfa' not in self.driver.current_url:
            return False
        return True


    def submit_mfa(self, code: str):
        print(f" -> Submitting MFA code: {code}")
        self.driver.find_element(By.ID, 'auth-mfa-otpcode').send_keys(code)
        self.driver.find_element(By.ID, 'auth-mfa-remember-device').click()
        self.get_screenshot("mfa_filled")
        # self.driver.find_element(By.ID, 'auth-signin-button').click()
        self.driver.find_element(*AlexaShoppingList.SUBMIT_SELECTOR).click()

        time.sleep(5)
        if not self.login_requires_mfa():
            self._login_successful()


    def login(self, email: str, password: str):
        self._selenium_get("https://www."+self.amazon_url, (By.ID, 'nav-link-accountList'))
        self._selenium_wait_page_ready()

        account_menu = self.driver.find_element(By.ID, 'nav-link-accountList')
        account_menu.click()
        self._selenium_wait_page_ready()

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

        #time.sleep(5)
        self._selenium_wait_page_ready()
        if not self.login_requires_mfa():
            if len(self.driver.find_elements(By.CLASS_NAME, 'nav-link-accountList')) > 0:
                self._login_successful()
            else:
                print(" -> Login failed")
                self.get_screenshot()


    def _login_successful(self):
        self.is_authenticated = True
        self._save_session()


    def requires_login(self):
        if 'ap/signin' in self.driver.current_url:
            return True

        if len(self.driver.find_elements(By.CLASS_NAME, 'nav-action-signin-button')) > 0:
            return True

        if not self.is_authenticated:
            return True

        return False

    # ============================================================
    # Alexa lists


    def _ensure_driver_is_on_alexa_list(self, refresh: bool = False):
        list_url = "https://www."+self.amazon_url+"/alexaquantum/sp/alexaShoppingList?ref=nav_asl"
        if self.driver.current_url != list_url:
            self._selenium_get(list_url, (By.CLASS_NAME, 'list-header'))
        elif refresh == True:
            self.driver.refresh()
            self._selenium_wait_element((By.CLASS_NAME, 'list-header'))


    def get_alexa_list(self, refresh: bool = True):
        self._ensure_driver_is_on_alexa_list(refresh)
        time.sleep(5)
        self.get_screenshot("alexa_list")

        list_container = self.driver.find_element(By.CLASS_NAME, 'virtual-list')

        found = []
        last = None
        while True:
            list_items = list_container.find_elements(By.CLASS_NAME, 'item-title')
            if list_items:
                print(f"Found {len(list_items)} items: from {list_items[0].get_attribute('innerText')} to {list_items[-1].get_attribute('innerText')}")
            for item in list_items:
                # print(f"Found: {item.get_attribute('innerText')} - {item}")
                if item.get_attribute('innerText') not in found:
                    found.append(item.get_attribute('innerText'))
            if not list_items or last == list_items[-1]:
                # We've reached the end
                break
            last = list_items[-1]
            print("Scrolling...")
            self.driver.execute_script("arguments[0].scrollIntoView();", last)
            # scroll_origin = ScrollOrigin.from_element(last)
            # ActionChains(self.driver).scroll_from_origin(scroll_origin, 0, 100).perform()
            time.sleep(1)

        if not refresh:
            # Now let's scroll back to the top
            first = None
            while True:
                list_items = list_container.find_elements(By.CLASS_NAME, 'item-title')
                if first == list_items[0]:
                    # We've reached the top
                    break
                first = list_items[0]
                scroll_origin = ScrollOrigin.from_element(first)
                ActionChains(self.driver).scroll_from_origin(scroll_origin, 0, -1000).perform()

        return found


    def _get_alexa_list_item_element(self, item: str):
        self._ensure_driver_is_on_alexa_list(False)
        time.sleep(5)
        list_container = self.driver.find_element(By.CLASS_NAME, 'virtual-list')

        for container in list_container.find_elements(By.CLASS_NAME, 'inner'):
            if container.find_element(By.CLASS_NAME, 'item-title').get_attribute('innerText') == item:
                return container
        return None


    def add_alexa_list_item(self, item: str):
        element = self._get_alexa_list_item_element(item)
        if element != None:
            return

        self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-symbol').click()

        textfield = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'input-box').find_element(By.TAG_NAME, 'input')
        textfield.send_keys(item)

        submit = self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'add-to-list').find_element(By.TAG_NAME, 'button')
        submit.click()

        self.driver.find_element(By.CLASS_NAME, 'list-header').find_element(By.CLASS_NAME, 'cancel-input').click()
        time.sleep(1)

        return self.get_alexa_list(False)


    def update_alexa_list_item(self, old: str, new: str):
        element = self._get_alexa_list_item_element(old)
        if element == None:
            return

        element.find_element(By.CLASS_NAME, 'item-actions-1').find_element(By.TAG_NAME, 'button').click()

        textfield = element.find_element(By.CLASS_NAME, 'input-box').find_element(By.TAG_NAME, 'input')
        textfield.clear()
        textfield.send_keys(new)

        element.find_element(By.CLASS_NAME, 'item-actions-2').find_element(By.TAG_NAME, 'button').click()
        time.sleep(1)

        return self.get_alexa_list(False)


    def remove_alexa_list_item(self, item: str):
        element = self._get_alexa_list_item_element(item)
        if element == None:
            return

        element.find_element(By.CLASS_NAME, 'item-actions-2').find_element(By.TAG_NAME, 'button').click()
        time.sleep(1)

        return self.get_alexa_list(False)

    # ============================================================
