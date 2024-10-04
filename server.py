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
        del alexa

    alexa = None
    alexa_running = False

# Make sure the length of a given string is a multiple of 8.
# If it isn't, pad it with '=' characters until it is.
def _pad_string(string):
    while len(string) % 8 != 0:
        string += '='
    return string


alexa_running = False
alexa = None
global config
config = _load_config()


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
    sys.exit(1)

logger.info("Connecting to Alexa...")
instance = _start_alexa()
logger.info("Logging in...")
instance.login(_get_config_value("amazon_username", "amazon_username"), _get_config_value("amazon_password", "amazon_password"))

if instance.login_requires_mfa():
    logger.info("Requires MFA")
    my_token = otp.get_totp(_pad_string(_get_config_value("amazon_mfa_secret", "amazon_mfa_secret")))
    instance.submit_mfa(my_token)
    if instance.is_authenticated == True:
        logger.info("Code accepted")
    else:
        logger.info("Code failed")

if instance.is_authenticated == True:
    logger.info("Logged in successfully")
else:
    logger.info("Login failed!!")
    _stop_alexa()
    anylist.teardown()
    sys.exit(1)

syncer = Synchronizer(list_anylist, instance, journal_file='journal.json')
while True:
    try:
        syncer.sync()
        sleep(10)
    except Exception as e:
        logger.error(e, exc_info=True)
        break

_stop_alexa()
anylist.teardown()


# Original Alexa list: ['Garbanzos 3.5kg', 'Alubia pinta 4kg']

# ~/anylist> podman build alexa2anylist/ -t alexa2anylist && podman run --rm -it -v ./config/:/config/ -v /etc/timezone:/etc/timezone:ro -v /etc/localtime:/etc/localtime:ro alexa2anylist
