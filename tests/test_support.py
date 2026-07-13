from __future__ import annotations

import sys
import types


def install_runtime_stubs() -> None:
    if "selenium" not in sys.modules:
        selenium = types.ModuleType("selenium")
        webdriver = types.ModuleType("selenium.webdriver")
        webdriver_common = types.ModuleType("selenium.webdriver.common")
        webdriver_common_by = types.ModuleType("selenium.webdriver.common.by")
        webdriver_common_keys = types.ModuleType("selenium.webdriver.common.keys")
        webdriver_common_actions = types.ModuleType("selenium.webdriver.common.actions")
        webdriver_common_actions_wheel = types.ModuleType("selenium.webdriver.common.actions.wheel_input")
        webdriver_chrome = types.ModuleType("selenium.webdriver.chrome")
        webdriver_chrome_options = types.ModuleType("selenium.webdriver.chrome.options")
        webdriver_support = types.ModuleType("selenium.webdriver.support")
        webdriver_support_ui = types.ModuleType("selenium.webdriver.support.ui")
        webdriver_support_ec = types.ModuleType("selenium.webdriver.support.expected_conditions")
        selenium_common = types.ModuleType("selenium.common")
        selenium_common_exceptions = types.ModuleType("selenium.common.exceptions")

        class ChromeService:
            def __init__(self, executable_path: str | None = None):
                self.executable_path = executable_path

        class Chrome:
            def __init__(self, service=None, options=None):
                self.service = service
                self.options = options

        class ActionChains:
            def __init__(self, driver):
                self.driver = driver

            def move_to_element(self, element):
                return self

            def click(self):
                return self

            def perform(self):
                return None

            def scroll_from_origin(self, *args, **kwargs):
                return self

        class By:
            CSS_SELECTOR = "css selector"
            TAG_NAME = "tag name"
            ID = "id"
            CLASS_NAME = "class name"
            LINK_TEXT = "link text"

        class ScrollOrigin:
            @staticmethod
            def from_element(element):
                return element

        class Options:
            def __init__(self):
                self.arguments = []
                self.capabilities = {}

            def add_argument(self, argument):
                self.arguments.append(argument)

            def set_capability(self, key, value):
                self.capabilities[key] = value

        class WebDriverWait:
            def __init__(self, driver, timeout):
                self.driver = driver
                self.timeout = timeout

            def until(self, condition):
                result = condition(self.driver)
                if callable(result):
                    result = result(self.driver)
                return result

        def _condition(*args, **kwargs):
            return lambda driver: True

        class ElementClickInterceptedException(Exception):
            pass

        class StaleElementReferenceException(Exception):
            pass

        class Keys:
            RETURN = "\n"

        webdriver.ChromeService = ChromeService
        webdriver.Chrome = Chrome
        webdriver.ActionChains = ActionChains
        webdriver_common_by.By = By
        webdriver_common_keys.Keys = Keys
        webdriver_common_actions_wheel.ScrollOrigin = ScrollOrigin
        webdriver_chrome_options.Options = Options
        webdriver_support_ui.WebDriverWait = WebDriverWait
        webdriver_support_ec.presence_of_element_located = _condition
        webdriver_support_ec.element_to_be_clickable = _condition
        selenium_common_exceptions.ElementClickInterceptedException = ElementClickInterceptedException
        selenium_common_exceptions.StaleElementReferenceException = StaleElementReferenceException

        selenium.webdriver = webdriver
        selenium.webdriver.common = webdriver_common
        selenium.webdriver.common.by = webdriver_common_by
        selenium.webdriver.common.keys = webdriver_common_keys
        selenium.webdriver.common.actions = webdriver_common_actions
        selenium.webdriver.common.actions.wheel_input = webdriver_common_actions_wheel
        selenium.webdriver.chrome = webdriver_chrome
        selenium.webdriver.chrome.options = webdriver_chrome_options
        selenium.webdriver.support = webdriver_support
        selenium.webdriver.support.ui = webdriver_support_ui
        selenium.webdriver.support.expected_conditions = webdriver_support_ec
        selenium.common = selenium_common
        selenium.common.exceptions = selenium_common_exceptions

        sys.modules["selenium"] = selenium
        sys.modules["selenium.webdriver"] = webdriver
        sys.modules["selenium.webdriver.common"] = webdriver_common
        sys.modules["selenium.webdriver.common.by"] = webdriver_common_by
        sys.modules["selenium.webdriver.common.keys"] = webdriver_common_keys
        sys.modules["selenium.webdriver.common.actions"] = webdriver_common_actions
        sys.modules["selenium.webdriver.common.actions.wheel_input"] = webdriver_common_actions_wheel
        sys.modules["selenium.webdriver.chrome"] = webdriver_chrome
        sys.modules["selenium.webdriver.chrome.options"] = webdriver_chrome_options
        sys.modules["selenium.webdriver.support"] = webdriver_support
        sys.modules["selenium.webdriver.support.ui"] = webdriver_support_ui
        sys.modules["selenium.webdriver.support.expected_conditions"] = webdriver_support_ec
        sys.modules["selenium.common"] = selenium_common
        sys.modules["selenium.common.exceptions"] = selenium_common_exceptions

    if "websocket" not in sys.modules:
        websocket = types.ModuleType("websocket")

        class WebSocketApp:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def run_forever(self, *args, **kwargs):
                return None

            def close(self):
                return None

        class WebSocketConnectionClosedException(Exception):
            pass

        websocket.WebSocketApp = WebSocketApp
        websocket.WebSocketConnectionClosedException = WebSocketConnectionClosedException
        sys.modules["websocket"] = websocket

    if "pcov_pb2" not in sys.modules:
        pcov_pb2 = types.ModuleType("pcov_pb2")

        class _Message:
            def __init__(self, *args, **kwargs):
                pass

            def SerializeToString(self):
                return b""

            def ParseFromString(self, data):
                return None

        pcov_pb2.PBUserDataResponse = _Message
        pcov_pb2.PBListOperation = _Message
        pcov_pb2.PBOperationMetadata = _Message
        pcov_pb2.PBListOperationList = _Message
        sys.modules["pcov_pb2"] = pcov_pb2

    if "onetimepass" not in sys.modules:
        onetimepass = types.ModuleType("onetimepass")

        def get_totp(secret):
            return "654321"

        onetimepass.get_totp = get_totp
        sys.modules["onetimepass"] = onetimepass
