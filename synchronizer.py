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
                self._journal.save()
        else:
            self.log.debug("Journal is clean, nothing to do")

        # Supposedly we're in sync now, let's check
        if not self._are_lists_equal(self._anylist_list, self._alexa_list):
            # If we're not, then we have no choice but to treat Anylist as the good list
            self.log.info("Lists are not in sync, clobbering Alexa...")
            self._clobber_alexa()
        else:
            self._seed_baselines()

    def _show_lists(self, title, a, b):
        if isinstance(a, list):
            self.log.debug(f"{title} Anylist: {sorted(a)[:15]}")
        else:
            self.log.debug(f"{title} Anylist: {sorted([x.name if not x.checked else f'x-{x.name}-' for x in a.items])[:15]}")
        self.log.debug(f"{title} Alexa: {sorted(b)}")

    def _are_lists_equal(self, a, b):
        if isinstance(a, list):
            return sorted(a) == sorted(b)
        else:
            anylist_items = sorted({x.name for x in a.items if not x.checked})
            alexa_items = sorted(set(b))
            return anylist_items == alexa_items

    def _get_fresh_lists(self):
        self.log.info("Getting fresh lists")
        a, b = (
            self.anylist.refresh(),
            self.alexa.get_alexa_list(refresh=True)
        )
        self._show_lists("Fresh", a, b)
        return a, b
        # return (
        #     self.anylist.refresh(),
        #     self.alexa.get_alexa_list()
        # )

    # Match the format of text written in Anylist to reduce duplicate entries
    def standardize_text(self, text):
        if not text:
            return text
        return text[0].upper() + text[1:]

    def _refresh_baselines(self):
        self._anylist_list, self._alexa_list = self._get_fresh_lists()
        self._old_anylist_list = self._anylist_list
        self._old_alexa_list = self._alexa_list

    def _seed_baselines(self):
        self._old_anylist_list = self._anylist_list
        self._old_alexa_list = self._alexa_list

    def _list_get_item_by_id(self, lst, item_id):
        getter = getattr(lst, 'get_item_by_id', None)
        if callable(getter):
            return getter(item_id)
        return None

    def _item_name(self, item):
        return getattr(item, 'name', None)

    def _item_checked(self, item):
        return bool(getattr(item, 'checked', False))

    def _require_alexa_item_state(self, updated_list, item_name, present, action):
        if updated_list is None:
            raise Exception(f"Failed to {action} {item_name} in Alexa")

        has_item = item_name in updated_list
        if has_item != present:
            state = 'present' if present else 'absent'
            raise Exception(f"Failed to {action} {item_name} in Alexa: item not {state} after update")

        return updated_list

    def _run_pending_transaction_if_needed(self):
        if not self._journal.is_dirty:
            return

        if time.time() - self._journal.last_update_time < 60 * 10:
            self.log.warning("Found dirty transaction, committing...")
            self._commit_transaction()
        else:
            self.log.warning("Found dirty transaction, but it's too old so we're ignoring it")
            self._journal.reset()
            self._journal.save()

    def _clobber_alexa(self):
        self.log.info("Clobbering Alexa with Anylist")
        new_alexa_list = self._alexa_list[:]
        # Anylist is the master list, add or delete items from Alexa
        for item in self._anylist_list:
            if item.checked and item.name in new_alexa_list:
                self.log.debug(f" -> Removing {item.name} from Alexa")
                updated_list = self.alexa.remove_alexa_list_item(item.name)
                if updated_list is None:
                    raise Exception(f"Failed to remove {item.name} from Alexa")
                new_alexa_list = updated_list
            elif not item.checked and item.name not in new_alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                updated_list = self.alexa.add_alexa_list_item(item.name)
                if updated_list is None:
                    raise Exception(f"Failed to add {item.name} to Alexa")
                new_alexa_list = updated_list

        # If there's anything in the Alexa list that's not in the Anylist, delete it
        # the [:] is to make a copy of the list so we can remove items from it while iterating
        for item in new_alexa_list[:]:
            if not self._anylist_list.get_item_by_name(item):
                self.log.debug(f" -> Removing {item} from Alexa")
                updated_list = self.alexa.remove_alexa_list_item(item)
                if updated_list is None:
                    raise Exception(f"Failed to remove {item} from Alexa")
                new_alexa_list = updated_list

        self._alexa_list = new_alexa_list
        self._old_anylist_list = self._anylist_list
        self._old_alexa_list = self._alexa_list

    def sync(self):
        self._run_pending_transaction_if_needed()
        self._show_lists("Old", self._old_anylist_list, self._old_alexa_list)

        self.log.info("Syncing lists")
        self._anylist_list, self._alexa_list = self._get_fresh_lists()

        if self._are_lists_equal(self._anylist_list, self._alexa_list):
            self._seed_baselines()
            self.log.info("Lists are already in sync")
            return

        self._prepare_transaction()
        self._commit_transaction()
        self.log.info("Sync complete")

    def _prepare_transaction(self):
        # Let's start the transaction
        self._journal.reset()

        # Let's see what's changed in Anylist
        for item in self._anylist_list:
            if item in self._old_anylist_list:
                old_item = self._list_get_item_by_id(self._old_anylist_list, item.identifier)
                if old_item is None:
                    continue
                if self._item_checked(item) != self._item_checked(old_item):
                    if self._item_checked(item):
                        self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_CHECKED_ITEMS, item.identifier)
                    else:
                        self._journal.add(Synchronizer.JOURNAL_KEY_ANYLIST_UNCHECKED_ITEMS, item.identifier)
                elif self._item_name(item) != self._item_name(old_item):
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
        new_alexa_list = self._alexa_list[:]
        # Ok, now we make the changes
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_NEW_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item is None:
                continue
            if item.name not in new_alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                updated_list = self.alexa.add_alexa_list_item(item.name)
                new_alexa_list = self._require_alexa_item_state(updated_list, item.name, True, 'add')
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_CHECKED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item is None:
                continue
            if item.name in new_alexa_list:
                self.log.debug(f" -> Removing {item.name} from Alexa")
                updated_list = self.alexa.remove_alexa_list_item(item.name)
                new_alexa_list = self._require_alexa_item_state(updated_list, item.name, False, 'remove')
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_UNCHECKED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item is None:
                continue
            if item.name not in new_alexa_list:
                self.log.debug(f" -> Adding {item.name} to Alexa")
                updated_list = self.alexa.add_alexa_list_item(item.name)
                new_alexa_list = self._require_alexa_item_state(updated_list, item.name, True, 'add')
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_RENAMED_ITEMS):
            item = self._anylist_list.get_item_by_id(item_id)
            if item is None:
                continue
            old_item = self._list_get_item_by_id(self._old_anylist_list, item.identifier)
            if old_item is None:
                continue
            old_name = self._item_name(old_item)
            item_name = self._item_name(item)
            if old_name in new_alexa_list and item_name not in new_alexa_list:
                self.log.debug(f" -> Updating {old_name} to {item_name} in Alexa")
                updated_list = self.alexa.update_alexa_list_item(old_name, item_name)
                updated_list = self._require_alexa_item_state(updated_list, old_name, False, 'rename')
                new_alexa_list = self._require_alexa_item_state(updated_list, item_name, True, 'rename')
        for item_id in self._journal.get(Synchronizer.JOURNAL_KEY_ANYLIST_DELETED_ITEMS):
            item = self._list_get_item_by_id(self._old_anylist_list, item_id)
            if item is None:
                continue
            item_name = self._item_name(item)
            if item_name in new_alexa_list:
                self.log.debug(f" -> Removing {item_name} from Alexa")
                updated_list = self.alexa.remove_alexa_list_item(item_name)
                new_alexa_list = self._require_alexa_item_state(updated_list, item_name, False, 'remove')

        for item in self._journal.get(Synchronizer.JOURNAL_KEY_ALEXA_NEW_ITEMS):
            # Alexa adds items in all lowercase, let's capitalize the first letter to reduce duplicates on Anylist
            s_item = self.standardize_text(item)
            if item != s_item:
                updated_list = self.alexa.update_alexa_list_item(item, s_item)
                updated_list = self._require_alexa_item_state(updated_list, item, False, 'rename')
                new_alexa_list = self._require_alexa_item_state(updated_list, s_item, True, 'rename')
                item = s_item
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
        self._alexa_list = new_alexa_list
        self._refresh_baselines()
