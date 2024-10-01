import os
import uuid
import requests
import websocket
import threading
import time
import pcov_pb2
import logging
import http.client as http_client
import json

logging.basicConfig(
    format='%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
# logger = logging.getLogger('__name__')
# logger.setLevel(logging.DEBUG)

class AnyList:
    CREDENTIALS_KEY_CLIENT_ID = 'clientId'
    CREDENTIALS_KEY_ACCESS_TOKEN = 'accessToken'
    CREDENTIALS_KEY_REFRESH_TOKEN = 'refreshToken'
    CREDENTIALS_LAST_UPDATED = 'lastUpdated'
    CREDENTIALS_LAST_UPDATED_METHOD = 'lastUpdatedMethod'
    ANYLIST_API = 'www.anylist.com'

    def __init__(self, email, password, credential_cache = None):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self.email = email
        self.password = password
        self.credentials_cache = credential_cache
        self.client_id = uuid.uuid4().hex
        self.access_token = None
        self.refresh_token = None
        self.lists = []
        self._old_lists = []
        self.last_updated = None
        self.changes_added = {}
        self.changes_modified = {}
        self.changes_deleted = {}
        self._user_data = None
        self.ws = None
        self.ws_connected = False

    def login(self):
        if not self._load_credentials():
            self._fetch_tokens()
        self._setup_websocket()
        self._get_user_data()
        self.get_lists()

    def _load_credentials(self):
        if not self.credentials_cache:
            return False

        config_path = os.environ.get(
            "CONFIG_PATH",
            os.path.dirname(os.path.realpath(__file__))
        )
        if os.path.exists(os.path.join(config_path, self.credentials_cache)):
            with open(os.path.join(config_path, self.credentials_cache), 'r') as file:
                credentials = json.load(file)

            self.client_id = credentials.get(AnyList.CREDENTIALS_KEY_CLIENT_ID, self.client_id)
            self.access_token = credentials.get(AnyList.CREDENTIALS_KEY_ACCESS_TOKEN)
            self.refresh_token = credentials.get(AnyList.CREDENTIALS_KEY_REFRESH_TOKEN)
            self.log.info("Loaded credentials from cache")
            return True

        return False

    def _save_credentials(self, method):
        if not self.credentials_cache:
            return False

        config_path = os.environ.get(
            "CONFIG_PATH",
            os.path.dirname(os.path.realpath(__file__))
        )
        with open(os.path.join(config_path, self.credentials_cache), 'w') as file:
            json.dump({
                AnyList.CREDENTIALS_KEY_CLIENT_ID: self.client_id,
                AnyList.CREDENTIALS_KEY_ACCESS_TOKEN: self.access_token,
                AnyList.CREDENTIALS_KEY_REFRESH_TOKEN: self.refresh_token,
                AnyList.CREDENTIALS_LAST_UPDATED: time.time(),
                AnyList.CREDENTIALS_LAST_UPDATED_METHOD: method,
            }, file)

        return True

    def _fetch_tokens(self):
        response = requests.post(f'https://{AnyList.ANYLIST_API}/auth/token', data={
            'email': self.email,
            'password': self.password,
        }, headers = {
            'X-AnyLeaf-API-Version': '3',
        })
        if response.status_code != 200:
            raise Exception(f"Failed to fetch tokens: {response.text}")

        result = response.json()
        self.access_token = result['access_token']
        self.refresh_token = result['refresh_token']
        self._save_credentials(method = 'fetch')
        self.log.info("Fetched tokens")

    def _refresh_tokens(self):
        response = requests.post(f'https://{AnyList.ANYLIST_API}/auth/token/refresh', data={
            'refresh_token': self.refresh_token,
        }, headers = {
            'X-AnyLeaf-API-Version': '3',
        })

        if response.status_code != 200:
            self.log.warning(f"Failed to refresh tokens: {response.text}")
            self.log.warning("Attempting to fetch new tokens using credentials")
            return self._fetch_tokens()

        result = response.json()
        self.access_token = result['access_token']
        self.refresh_token = result['refresh_token']
        self._save_credentials(method = 'refresh')
        self.log.info("Refreshed tokens")
        if not self.ws_connected:
            self._setup_websocket()

    def _setup_websocket(self):
        def on_message(ws, message):
            if message == '--heartbeat--':
                # Ignore heartbeats
                return

            self.log.debug(f"Received message: {message}")
            if (message == 'refresh-shopping-lists'):
                self.log.debug('Refreshing shopping lists')
                self.get_lists(refresh_cache=True)

        def on_error(ws, error):
            self.log.error(f"WebSocket error: {error}")
            self._refresh_tokens()

        def on_close(ws, close_status_code, close_msg):
            self.ws_connected = False
            if close_status_code or close_msg:
                self.log.info(f"WebSocket closed: {close_status_code} {close_msg}")
            else:
                self.log.info("WebSocket closed")

        def on_open(ws):
            self.ws_connected = True
            self.log.info("WebSocket connection opened")

            # def send_heartbeat():
            #     while True:
            #         time.sleep(5)
            #         try:
            #             ws.send('--heartbeat--')
            #             #self.log.debug("Sent heartbeat")
            #         except websocket.WebSocketConnectionClosedException:
            #             self.log.info("WebSocket connection closed, stopping heartbeat")
            #             break

            # heartbeat_thread = threading.Thread(target=send_heartbeat)
            # heartbeat_thread.daemon = True
            # heartbeat_thread.start()

        def on_ping(ws, data):
            self.log.debug(f"Received ping: {data}")

        # def on_pong(ws, data):
        #     self.log.debug(f"Received pong: {data}")

        self.ws = websocket.WebSocketApp(
            f'wss://{AnyList.ANYLIST_API}/data/add-user-listener',
            header={
                'Authorization': f'Bearer {self.access_token}',
                'X-AnyLeaf-Client-Identifier': self.client_id,
                'X-AnyLeaf-API-Version': '3',
            },
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
            on_ping=on_ping,
            # on_pong=on_pong,
        )
        wst = threading.Thread(
            target=self.ws.run_forever,
            kwargs={'ping_interval': 5, 'ping_payload': '--heartbeat--'},)
        wst.daemon = True
        wst.start()

    def teardown(self):
        # Close the websocket connection
        self.ws.close()

    def _post(self, path, data = {}, files = {}, headers = {}):
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'X-AnyLeaf-Client-Identifier': self.client_id,
            'X-AnyLeaf-API-Version': '3',
        } | headers

        def _request():
            if files:
                return requests.post(f'https://{AnyList.ANYLIST_API}{path}', files=files, headers=headers)

            return requests.post(f'https://{AnyList.ANYLIST_API}{path}', data=data, headers=headers)

        response = _request()
        if response.status_code != 200:
            self.log.warning(f"Failed to send request, will retry: {response.text}")
            # Try refreshing the tokens and try again
            self._refresh_tokens()
            time.sleep(5)
            response = _request()
            if response.status_code != 200:
                raise Exception(f"Failed to send request: {response.text}")

        return response


    def _get_user_data(self, refresh_cache=False):
        if self._user_data and not refresh_cache:
            return self._user_data

        response = self._post('/data/user-data/get')
        if response.status_code != 200:
            raise Exception(f"Failed to get user data: {response.text}")

        self._user_data = pcov_pb2.PBUserDataResponse()
        self._user_data.ParseFromString(response.content)
        self.last_updated = time.time()
        return self._user_data

    def get_lists(self, refresh_cache=False):
        if self.lists and not refresh_cache:
            return self.lists

        # Only update the old lists if the last update was more than a second ago
        # Sometimes updates are sent in quick succession, so we don't want to overwrite the old lists
        # because we'll miss changes.
        if self.last_updated and time.time() - self.last_updated > 1:
            self._old_lists = self.lists

        self._get_user_data(refresh_cache)
        self.lists = [List(self, l) for l in self._user_data.shoppingListsResponse.newLists]

        self.changes_added = {}
        self.changes_modified = {}
        self.changes_deleted = {}
        if self._old_lists:
            for lst in self.lists:
                for i in lst.items:
                    old = next((l for l in self._old_lists if l.identifier == lst.identifier), None)
                    if not old or i not in old:
                        if not lst.identifier in self.changes_added:
                            self.changes_added[lst.identifier] = []
                        self.changes_added[lst.identifier].append(i)
                        # self.log.debug(f"Added item {i} to list {lst}")
                    elif i != old.get_item_by_id(i.identifier):
                        if not lst.identifier in self.changes_modified:
                            self.changes_modified[lst.identifier] = []
                        self.changes_modified[lst.identifier].append(i)
                        # self.log.debug(f"Modified item {i} in list {lst}")
            for lst in self._old_lists:
                for i in lst.items:
                    if i not in self.get_list_by_id(lst.identifier):
                        if not lst.identifier in self.changes_deleted:
                            self.changes_deleted[lst.identifier] = []
                        self.changes_deleted[lst.identifier].append(i)
                        # self.log.debug(f"Deleted item {i} from list {lst}")

        return self.lists

    def get_list_by_id(self, identifier):
        return next((lst for lst in self.lists if lst.identifier == identifier), None)

    def get_list_by_name(self, name):
        return next((lst for lst in self.lists if lst.name == name), None)

    def get_recent_items_by_list_id(self, list_id):
        return self.recent_items.get(list_id, [])


class List:

    def __init__(self, api, list_data):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self._api = api
        self._pb = list_data
        self.identifier = list_data.identifier
        self.name = list_data.name
        self.items = [Item(self, i) for i in list_data.items]
        self.creator = list_data.creator

    def __repr__(self) -> str:
        return f"List('{self.name}', {len(self.items)} items, {self.identifier})"

    def __contains__(self, item):
        return self.get_item_by_id(item.identifier) is not None

    def __iter__(self):
        yield from self.items

    def __getitem__(self, key):
        return self.items[key]

    def __setitem__(self, key, value):
        if not isinstance(value, Item):
            raise Exception("Value must be an Item")
        self.items[key] = value

    def __len__(self):
        return len(self.items)

    def _execute(self, ops):
        return self._api._post('/data/shopping-lists/update', files={
            'operations': (None, ops.SerializeToString()),
        })

    def refresh(self):
        # self._api.get_lists(refresh_cache=True)
        return self._api.get_list_by_id(self.identifier)

    def get_item_by_id(self, identifier):
        return next((i for i in self.items if i.identifier == identifier), None)

    def get_item_by_name(self, name):
        return next((i for i in self.items if i.name == name), None)

    def add_item(self, item):
        if isinstance(item, str):
            item = Item.from_name(self, item)
        else:
            item = Item(self, item)

        item.listId = self.identifier

        op = pcov_pb2.PBListOperation()
        op.listId = self.identifier
        op.listItemId = item.identifier
        op.listItem.CopyFrom(item.asPB())

        metadata = pcov_pb2.PBOperationMetadata()
        metadata.operationId = uuid.uuid4().hex
        metadata.handlerId = 'add-shopping-list-item'
        metadata.userId = self.creator
        op.metadata.CopyFrom(metadata)

        ops = pcov_pb2.PBListOperationList()
        ops.operations.append(op)

        response = self._execute(ops)

        if response.status_code != 200:
            raise Exception(f"Failed to add item: {response.text}")

        self.items.append(item)
        self.log.debug(f"Added item {item} to list {self}")

    def remove_item(self, item):
        if isinstance(item, str):
            item = self.get_item_by_name(item)

        op = pcov_pb2.PBListOperation()
        op.listId = self.identifier
        op.listItemId = item.identifier
        op.listItem.CopyFrom(item.asPB())

        metadata = pcov_pb2.PBOperationMetadata()
        metadata.operationId = uuid.uuid4().hex
        metadata.handlerId = 'remove-shopping-list-item'
        metadata.userId = self.creator
        op.metadata.CopyFrom(metadata)

        ops = pcov_pb2.PBListOperationList()
        ops.operations.append(op)

        response = self._execute(ops)

        if response.status_code != 200:
            raise Exception(f"Failed to remove item: {response.text}")

        self.items.remove(item)
        self.log.debug(f"Removed item {item} from list {self}")

    def _get_item(self, item):
        if isinstance(item, str):
            return self.get_item_by_name(item)
        else:
            return self.get_item_by_id(item.identifier)

    def check_item(self, item):
        item = self._get_item(item)
        item.checked = True
        if item.save():
            self.log.debug(f"Checked item {item} in list {self}")

    def uncheck_item(self, item):
        item = self._get_item(item)
        item.checked = False
        if item.save():
            self.log.debug(f"Unchecked item {item} in list {self}")

    def toggle_item(self, item):
        item = self._get_item(item)
        item.checked = not item.checked
        if item.save():
            self.log.debug(f"Toggled item {item} in list {self}")

    def add_or_uncheck_item(self, item):
        _item = self._get_item(item)

        if not _item:
            self.add_item(item)
        elif _item.checked:
            _item.checked = False
            if _item.save():
                self.log.debug(f"Unchecked item {_item} in list {self}")

    def uncheck_all(self):
        op = pcov_pb2.PBListOperation()
        op.listId = self.identifier

        metadata = pcov_pb2.PBOperationMetadata()
        metadata.operationId = uuid.uuid4().hex
        metadata.handlerId = 'uncheck-all'
        metadata.userId = self.creator
        op.metadata.CopyFrom(metadata)

        ops = pcov_pb2.PBListOperationList()
        ops.operations.append(op)

        response = self._execute(ops)

        if response.status_code != 200:
            raise Exception(f"Failed to uncheck all items: {response.text}")

        self.log.debug(f"Unchecked all items in list {self}")


class Item:

    OP_MAPPING = {
        'name': 'set-list-item-name',
        'quantity': 'set-list-item-quantity',
        'details': 'set-list-item-details',
        'checked': 'set-list-item-checked',
        'categoryMatchId': 'set-list-item-category-match-id',
        'manualSortIndex': 'set-list-item-sort-order',
    }

    def __init__(self, lst, item_data):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self._list = lst
        self._pb = item_data
        self._identifier = item_data.identifier
        self._listId = item_data.listId or lst.identifier
        self._name = item_data.name
        self._quantity = item_data.quantity
        self._details = item_data.details
        self._checked = item_data.checked
        self._category = item_data.category
        self._userId = item_data.userId
        self._categoryMatchId = item_data.categoryMatchId
        self._manualSortIndex = item_data.manualSortIndex

        self._fieldsToUpdate = []

    @classmethod
    def from_name(cls, lst, name):
        item = pcov_pb2.ListItem()
        item.identifier = uuid.uuid4().hex
        item.name = name
        return cls(lst, item)

    def __repr__(self) -> str:
        return f"Item('{self.name}', {self.identifier})"

    def __eq__(self, other):
        try:
            return self.identifier == other.identifier and self.name == other.name and self.checked == other.checked
        except AttributeError:
            return False

    def save(self):
        ops = pcov_pb2.PBListOperationList()

        for field in self._fieldsToUpdate:
            value = getattr(self, field)

            op = pcov_pb2.PBListOperation()
            op.listId = self._listId
            op.listItemId = self._identifier

            if isinstance(value, bool):
                op.updatedValue = 'y' if value else 'n'
            else:
                op.updatedValue = str(value)

            metadata = pcov_pb2.PBOperationMetadata()
            metadata.operationId = uuid.uuid4().hex
            metadata.handlerId = Item.OP_MAPPING[field]
            metadata.userId = self._userId
            op.metadata.CopyFrom(metadata)

            ops.operations.append(op)

        if ops.operations:
            response = self._list._execute(ops)

            if response.status_code != 200:
                self.log.warning(f"Failed to update item first time: {response.text}")
                # Reauthenticate and try again
                self._list._api._refresh_tokens()

                response = self._list._execute(ops)

                if response.status_code != 200:
                    # Ok, now we're really screwed
                    raise Exception(f"Failed to update item: {response.text}")

            self._fieldsToUpdate.clear()
            self.log.debug(f"Updated item {self} in list {self._listId}")
            return True

        return False

    @property
    def identifier(self):
        return self._identifier

    @identifier.setter
    def identifier(self, value):
        raise Exception("Cannot set identifier after creation")

    @property
    def listId(self):
        return self._listId

    @listId.setter
    def listId(self, value):
        if not self._listId:
            self._listId = value
            self._fieldsToUpdate.append('listId')
        elif self._listId != value:
            raise Exception("Cannot change listId")

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if self._name != value:
            self._name = value
            self._fieldsToUpdate.append('name')

    @property
    def quantity(self):
        return self._quantity

    @quantity.setter
    def quantity(self, value):
        if self._quantity != value:
            self._quantity = value
            self._fieldsToUpdate.append('quantity')

    @property
    def details(self):
        return self._details

    @details.setter
    def details(self, value):
        if self._details != value:
            self._details = value
            self._fieldsToUpdate.append('details')

    @property
    def checked(self):
        return self._checked

    @checked.setter
    def checked(self, value):
        if not isinstance(value, bool):
            raise Exception("Checked must be a boolean")

        if self._checked != value:
            self._checked = value
            self._fieldsToUpdate.append('checked')

    @property
    def category(self):
        return self._category

    @category.setter
    def category(self, value):
        if self._category != value:
            self._category = value
            self._fieldsToUpdate.append('category')

    @property
    def userId(self):
        return self._userId

    @userId.setter
    def userId(self, value):
        raise Exception("Cannot set userId after creation")

    @property
    def categoryMatchId(self):
        return self._categoryMatchId

    @categoryMatchId.setter
    def categoryMatchId(self, value):
        if self._categoryMatchId != value:
            self._categoryMatchId = value
            self._fieldsToUpdate.append('categoryMatchId')

    @property
    def manualSortIndex(self):
        return self._manualSortIndex

    @manualSortIndex.setter
    def manualSortIndex(self, value):
        if not isinstance(value, int):
            raise Exception("ManualSortIndex must be an integer")

        if self._manualSortIndex != value:
            self._manualSortIndex = value
            self._fieldsToUpdate.append('manualSortIndex')

    def asPB(self):
        item = pcov_pb2.ListItem()
        item.identifier = self._identifier
        item.listId = self._listId
        item.name = self._name
        item.quantity = self._quantity
        item.details = self._details
        item.checked = self._checked
        item.category = self._category
        item.userId = self._userId
        item.categoryMatchId = self._categoryMatchId
        item.manualSortIndex = self._manualSortIndex
        self._pb = item
        return self._pb


# http_client.HTTPConnection.debuglevel = 1
# logging.basicConfig()
# logging.getLogger().setLevel(logging.DEBUG)
# requests_log = logging.getLogger("requests.packages.urllib3")
# requests_log.setLevel(logging.DEBUG)
# requests_log.propagate = True

if __name__ == "__main__":

    anylist = AnyList(email=os.getenv('ANYLIST_USERNAME'), password=os.getenv('ANYLIST_PASSWORD'))
    anylist.login()
    lists = anylist.get_lists()
    print(lists)
