import argparse
import json
import os
import sys
from alexa import AlexaShoppingList
from anylist import AnyList
from synchronizer import Synchronizer
import onetimepass as otp
from time import sleep
import traceback
import logging


logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('__name__')
logger.setLevel(logging.DEBUG)

def _config_path():
    return os.environ.get(
        "CONFIG_PATH",
        os.path.dirname(os.path.realpath(__file__))
    )

def _load_config():
    if os.path.exists(os.path.join(_config_path(), 'config.json')):
        with open(os.path.join(_config_path(), 'config.json'), 'r') as file:
            return json.load(file)
    return {}

def _get_config_value(key, default=None):
    if key in config.keys():
        return config[key]
    return default

def _start_alexa():
    global alexa
    global alexa_running

    if alexa_running == False:
        alexa = AlexaShoppingList(
            _get_config_value("amazon_url", "amazon.co.uk"),
            _config_path()
        )
        alexa_running = True

    return alexa

def _stop_alexa():
    global alexa
    global alexa_running

    if alexa_running == True:
        alexa._clear_driver()

    alexa = None
    alexa_running = False

# Make sure the length of a given string is a multiple of 8.
# If it isn't, pad it with '=' characters until it is.
def _pad_string(string):
    while len(string) % 8 != 0:
        string += '='
    return string


parser = argparse.ArgumentParser()
parser.add_argument("--once", action="store_true", help="Sync once and exit")
args = None

alexa_running = False
alexa = None
global config
config = {}


def _create_syncer():
    _anylist_cred_cache = os.path.join(_config_path(), 'anylist-credentials.json')
    if os.path.exists(_anylist_cred_cache):
        os.remove(_anylist_cred_cache)

    anylist = AnyList(
        email=_get_config_value("anylist_username", "anylist_username"),
        password=_get_config_value("anylist_password", "anylist_password"),
        credential_cache='anylist-credentials.json',
    )
    anylist.login()
    list_anylist = anylist.get_list_by_name(_get_config_value("anylist_list_name", "anylist_list_name"))
    logger.info(f"Anylist: {list_anylist}")
    if list_anylist is None:
        logger.info("List not found")
        anylist.teardown()
        raise RuntimeError("AnyList list not found")

    logger.info("Connecting to Alexa...")
    _alexa = _start_alexa()
    logger.info("Logging in...")
    _alexa.login(_get_config_value("amazon_username", "amazon_username"), _get_config_value("amazon_password", "amazon_password"))

    if _alexa.login_requires_mfa():
        logger.info("Requires MFA")
        my_token = otp.get_totp(_pad_string(_get_config_value("amazon_mfa_secret", "amazon_mfa_secret")))
        _alexa.submit_mfa(my_token)
        if _alexa.is_authenticated:
            logger.info("Code accepted")
        else:
            logger.info("Code failed")

    if not _alexa.is_authenticated:
        logger.info("Login failed!!")
        _stop_alexa()
        anylist.teardown()
        raise RuntimeError("Alexa login failed")

    logger.info("Logged in successfully")
    syncer = Synchronizer(list_anylist, _alexa, journal_file='journal.json')
    return anylist, syncer


def main(max_cycles=None, retry_delay=10, sync_delay=10):
    global config
    config = _load_config()

    run_once = bool(max_cycles == 1 or _get_config_value("run_once", False) or (args is not None and args.once))
    if max_cycles is None and run_once:
        max_cycles = 1

    cycle_count = 0
    anylist = None
    syncer = None

    while True:
        if max_cycles is not None and cycle_count >= max_cycles:
            break

        if syncer is None or anylist is None:
            anylist, syncer = _create_syncer()

        try:
            syncer.sync()
            cycle_count += 1
            if run_once:
                break
            sleep(sync_delay)
        except Exception as e:
            logger.error(e, exc_info=True)
            if anylist is not None:
                anylist.teardown()
            anylist = None
            syncer = None
            _stop_alexa()
            sleep(retry_delay)

    _stop_alexa()
    if anylist is not None:
        anylist.teardown()


if __name__ == "__main__":
    args = parser.parse_args()
    config = _load_config()
    if args.once:
        config["run_once"] = True
    try:
        main()
    except RuntimeError:
        sys.exit(1)


# Original Alexa list: ['Garbanzos 3.5kg', 'Alubia pinta 4kg']

# ~/alexa2anylist [main|✚ 2 …4]> rm config/anylist-credentials.json config/cookies.json -f
# ~/alexa2anylist [main|✚ 1 …4]> podman build . -t alexa2anylist && podman run --rm -it -v ./config/:/config/ -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro -v /tmp/out:/out alexa2anylist
