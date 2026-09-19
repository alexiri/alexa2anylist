import argparse
import json
import os
import sys
from alexa_api import AlexaAPI, AlexaShoppingList
from anylist import AnyList
from synchronizer import Synchronizer
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

parser = argparse.ArgumentParser()
parser.add_argument("--once", action="store_true", help="Sync once and exit")
args = None

global config
config = {}


def _create_syncer():
    anylist = AnyList(
        email=_get_config_value("anylist_username", "anylist_username"),
        password=_get_config_value("anylist_password", "anylist_password"),
        credential_cache='anylist-credentials.json',
        login_attempt_cache='anylist-login-attempt.json',
    )
    anylist.login()
    list_anylist = anylist.get_list_by_name(_get_config_value("anylist_list_name", "anylist_list_name"))
    logger.info(f"Anylist: {list_anylist}")
    if list_anylist is None:
        logger.info("List not found")
        anylist.teardown()
        raise RuntimeError("AnyList list not found")

    logger.info("Logging in to Alexa...")
    try:
        alexa = AlexaAPI(
            _get_config_value("amazon_url", "amazon.co.uk"),
            _get_config_value("amazon_username", "amazon_username"),
            _get_config_value("amazon_password", "amazon_password"),
            _get_config_value("amazon_mfa_secret", "amazon_mfa_secret"),
            credential_cache='alexa-credentials.json',
        )
        alexa.login()
        logger.info("Logged in successfully")
        syncer = Synchronizer(list_anylist, AlexaShoppingList(alexa), journal_file='journal.json')
    except Exception:
        anylist.teardown()
        raise
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

        try:
            if syncer is None or anylist is None:
                anylist, syncer = _create_syncer()

            syncer.sync()
            cycle_count += 1
            if run_once:
                break
            sleep(sync_delay)
        except Exception as e:
            cycle_count += 1
            logger.error(e, exc_info=True)
            if anylist is not None:
                anylist.teardown()
            anylist = None
            syncer = None
            sleep(retry_delay)

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

# ~/alexa2anylist [main|✚ 2 …4]> rm config/anylist-credentials.json config/alexa-credentials.json -f
# ~/alexa2anylist [main|✚ 1 …4]> podman build . -t alexa2anylist && podman run --rm -it -v ./config/:/config/ -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro alexa2anylist
