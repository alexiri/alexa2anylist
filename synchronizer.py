import logging
import os
import json
import time

class Journal:

    def __init__(self, journal_file=None):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self.reset()
        self._journal_file = journal_file
        self._load()

    def reset(self):
        self._data = {}
        self._dirty = False
        self._last_update_time = time.time()

    def add(self, key, value):
        self._dirty = True
        self._last_update_time = time.time()
        if not key in self._data:
            self._data[key] = []
        self._data[key].append(value)

    def get(self, key):
        return self._data.get(key, [])[:]

    def __str__(self):
        return str(self._data)

    @property
    def is_dirty(self):
        return self._dirty

    @property
    def last_update_time(self):
        return self._last_update_time

    def _load(self):
        if not self._journal_file:
            return

        try:
            with open(self._journal_file, 'r') as file:
                data = json.load(file)
                self._dirty = data.get('dirty', False)
                self._last_update_time = data.get('last_update_time', time.time())
                self._data = data.get('data', {})
        except Exception as e:
            self.log.error(f"Error loading journal from {self._journal_file}: {e}", exc_info=True)

    def save(self):
        if not self._journal_file:
            return

        try:
            with open(self._journal_file, 'w') as file:
                json.dump({
                    'dirty': self._dirty,
                    'last_update_time': self._last_update_time,
                    'data': self._data,
                }, file, indent=4)
        except Exception as e:
            self.log.error(f"Error saving journal to {self._journal_file}: {e}", exc_info=True)
            raise e


class Synchronizer:
    JOURNAL_KEY_LAST_UPDATE_TIME = "last_update_time"
    JOURNAL_KEY_TRANSACTION_STATE = "transaction_state"
    JOURNAL_KEY_ANYLIST_NEW_ITEMS = "anylist_new_items"
    JOURNAL_KEY_ANYLIST_CHECKED_ITEMS = "anylist_checked_items"
    JOURNAL_KEY_ANYLIST_UNCHECKED_ITEMS = "anylist_unchecked_items"
    JOURNAL_KEY_ANYLIST_RENAMED_ITEMS = "anylist_renamed_items"
    JOURNAL_KEY_ANYLIST_DELETED_ITEMS = "anylist_deleted_items"
    JOURNAL_KEY_ALEXA_NEW_ITEMS = "alexa_new_items"
    JOURNAL_KEY_ALEXA_DELETED_ITEMS = "alexa_deleted_items"

    # Master list is anylist, alexa is the slave
    def __init__(self, anylist, alexa, journal_file=None):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.DEBUG)
        self.anylist = anylist
        self.alexa = alexa
        self._old_anylist_list = []
        self._old_alexa_list = []
        self._anylist_list, self._alexa_list = self._get_fresh_lists()

        if not journal_file:
            self._journal = Journal()
        else:
            config_path = os.environ.get(
                "CONFIG_PATH",
                os.path.dirname(os.path.realpath(__file__))
            )
            self._journal = Journal(journal_file = os.path.join(config_path, journal_file))

        # We have a journal, so let's see if we had any transactions in progress
        if self._journal.is_dirty:
            # We died in the middle of a transaction, let's see if we can recover
            # If the transaction is more than 10 minutes old, we probably shouldn't commit it
            if time.time() - self._journal.last_update_time < 60 * 10:
                self.log.warning("Found dirty transaction, committing...")
                self._commit_transaction()
            else:
                self.log.warning("Found dirty transaction, but it's too old so we're ignoring it")
                self._journal.reset()
        else:
            self.log.debug("Journal is clean, nothing to do")

        # Supposedly we're in sync now, let's check
        if not self._compare_lists(self._anylist_list, self._alexa_list):
            # If we're not, then we have no choice but to treat Anylist as the good list
            self.log.info("Lists are not in sync, clobbering Alexa...")
            self._clobber_alexa()

    def _show_lists(self, title, a, b):
        if isinstance(a, list):
            self.log.debug(f"{title} Anylist: {sorted(a)}")
        else:
            self.log.debug(f"{title} Anylist: {sorted([x.name if not x.checked else f'x-{x.name}-' for x in a.items])}")
        self.log.debug(f"{title} Alexa: {sorted(b)}")

    def _compare_lists(self, a, b):
        if isinstance(a, list):
            return sorted(a) == sorted(b)
        else:
            return sorted([x.name for x in a.items if not x.checked]) == sorted(b)

    def _get_fresh_lists(self):
        self.log.info("Getting fresh lists")
        a, b = (
            self.anylist.refresh(),
            self.alexa.get_alexa_list()
        )
        self._show_lists("Fresh", a, b)
        return a, b
        # return (
        #     self.anylist.refresh(),
        #     self.alexa.get_alexa_list()
        # )

    def _clobber_alexa(self):
        self.log.info("Clobbering Alexa with Anylist")
        # Anylist is the master list, add or delete items from Alexa
        for item in self._anylist_list:
            if item.checked and item.name in self._alexa_list:
                self.log.debug(f" -> Removing {item.name} from Alexa")
                self.alexa.remove_alexa_list_item(item.name)
                self._alexa_list.remove(item.name)
            elif not item.checked and item.name not in self._alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                self.alexa.add_alexa_list_item(item.name)
                self._alexa_list.append(item.name)

        # If there's anything in the Alexa list that's not in the Anylist, delete it
        # the [:] is to make a copy of the list so we can remove items from it while iterating
        for item in self._alexa_list[:]:
            if not self._anylist_list.get_item_by_name(item):
                self.log.debug(f" -> Removing {item} from Alexa")
                self.alexa.remove_alexa_list_item(item)
                self._alexa_list.remove(item)

        self._old_anylist_list = self._anylist_list
        self._old_alexa_list = self._alexa_list

    def sync(self):
        self._show_lists("Old", self._old_anylist_list, self._old_alexa_list)

        self.log.info("Syncing lists")
        self._anylist_list, self._alexa_list = self._get_fresh_lists()

        self._prepare_transaction()
        self._commit_transaction()
        self.log.info("Sync complete")

    def _prepare_transaction(self):
        # Let's start the transaction
        self._journal.reset()

        # Let's see what's changed in Anylist
        for item in self._anylist_list:
            if item in self._old_anylist_list:
                old_item = self._old_anylist_list.get_item_by_id(item.identifier)
                if item.checked != old_item.checked:
                    if item.checked:
                        self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_CHECKED_ITEMS, item.identifier)
                    else:
                        self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_UNCHECKED_ITEMS, item.identifier)
                elif item.name != old_item.name:
                    self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_RENAMED_ITEMS, item.identifier)
            elif not item.checked:
                # if it's new but checked, we don't care
                self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_NEW_ITEMS, item.identifier)
        for item in self._old_anylist_list:
            if item not in self._anylist_list:
                self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_DELETED_ITEMS, item.identifier)

        # Now let's see what's changed in Alexa
        for item in self._alexa_list:
            if item not in self._old_alexa_list:
                self._journal.add(Synchronizer.JOURNAL_KEY_ALEXA_NEW_ITEMS, item)
        for item in self._old_alexa_list:
            if item not in self._alexa_list:
                self._journal.add(Synchronizer.JOURNAL_KEY_ALEXA_DELETED_ITEMS, item)

        # Write the journal, in case something goes wrong
        self._journal.save()
        self.log.debug(f"Transactions: {str(self._journal)}")

    def _commit_transaction(self):
        # Check, just in case...
        if not self._journal.is_dirty:
            self.log.info("Nothing to do")
            return

        self.log.debug("Committing transaction...")
        # Ok, now we make the changes
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_NEW_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item.name not in self._alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                self.alexa.add_alexa_list_item(item.name)
                self._alexa_list.append(item.name)
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_CHECKED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item.name in self._alexa_list:
                self.log.debug(f" -> Removing {item.name} from Alexa")
                self.alexa.remove_alexa_list_item(item.name)
                self._alexa_list.remove(item.name)
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_UNCHECKED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item.name not in self._alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                self.alexa.add_alexa_list_item(item.name)
                self._alexa_list.append(item.name)
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_RENAMED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            old_item = self._old_anylist_list.get_item_by_id(item.identifier)
            if old_item.name in self._alexa_list and item.name not in self._alexa_list:
                self.log.debug(f" -> Updating {old_item.name} to {item.name} in Alexa")
                self.alexa.update_alexa_list_item(old_item.name, item.name)
                self._alexa_list[self._alexa_list.index(old_item.name)] = item.name
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_DELETED_ITEMS):
            item = self._old_anylist_list.get_item_by_id(item_id)
            if item.name in self._alexa_list:
                self.log.debug(f" -> Removing {item.name} from Alexa")
                self.alexa.remove_alexa_list_item(item.name)
                self._alexa_list.remove(item.name)

        for item in self._journal.get(Synchronizer.JOURNAL_KEY_ALEXA_NEW_ITEMS):
            anylist_item = self._anylist_list.get_item_by_name(item)
            if not anylist_item or anylist_item.checked:
                self.log.debug(f" -> Adding {item} to Anylist")
                self._anylist_list.add_or_uncheck_item(item)
        for item in self._journal.get(Synchronizer.JOURNAL_KEY_ALEXA_DELETED_ITEMS):
            if self._anylist_list.get_item_by_name(item):
                self.log.debug(f" -> Checking {item} in Anylist")
                self._anylist_list.check_item(item)

        self._journal.reset()
        self._journal.save()
        self.log.debug("Transaction committed.")

        self._old_anylist_list = self._anylist_list
        self._old_alexa_list = self._alexa_list
