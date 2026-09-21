# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for the storage layer: JSON files, config and devices."""

import json
import hashlib
import os
import tempfile
import unittest

from kasapanel import config as config_lib
from kasapanel import devicestore
from kasapanel import jsonstore


class JsonStoreTest(unittest.TestCase):
    """Atomic JSON reading and writing."""

    def setUp(self):
        """Creates a scratch directory."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_round_trip(self):
        """A written document reads back unchanged."""
        path = os.path.join(self.tmp.name, 'nested', 'doc.json')
        jsonstore.write_json(path, {'b': 1, 'a': [1, 2]})
        self.assertEqual(jsonstore.read_json(path), {'a': [1, 2], 'b': 1})
        self.assertEqual(os.stat(path).st_mode & 0o777, jsonstore.FILE_MODE)

    def test_missing_file_returns_default(self):
        """Reading a file that is not there gives the default."""
        path = os.path.join(self.tmp.name, 'absent.json')
        self.assertEqual(jsonstore.read_json(path, {'ok': True}),
                         {'ok': True})

    def test_bad_json_raises(self):
        """A corrupt file is reported rather than silently replaced."""
        path = os.path.join(self.tmp.name, 'broken.json')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('{not json')
        with self.assertRaises(jsonstore.StoreError):
            jsonstore.read_json(path)

    def test_no_temporary_files_are_left(self):
        """The atomic write leaves nothing behind."""
        path = os.path.join(self.tmp.name, 'doc.json')
        jsonstore.write_json(path, {'a': 1})
        jsonstore.write_json(path, {'a': 2})
        self.assertEqual(os.listdir(self.tmp.name), ['doc.json'])

    def test_document_wrapper(self):
        """JsonDocument caches, replaces and reloads."""
        path = os.path.join(self.tmp.name, 'doc.json')
        document = jsonstore.JsonDocument(path, {'count': 0})
        self.assertEqual(document.data(), {'count': 0})
        document.replace({'count': 3})
        self.assertEqual(jsonstore.read_json(path), {'count': 3})
        self.assertEqual(jsonstore.JsonDocument(path).load(), {'count': 3})


class ConfigTest(unittest.TestCase):
    """Configuration loading, coercion and updates."""

    def setUp(self):
        """Creates a scratch directory and a config path in it."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'config.json')

    def _store(self) -> config_lib.ConfigStore:
        """Builds a store whose state lives in the scratch directory.

        Returns:
            The loaded store.
        """
        store = config_lib.ConfigStore(self.path)
        settings = store.load()
        settings.state_dir = os.path.join(self.tmp.name, 'state')
        store.save(settings)
        return store

    def test_first_load_writes_defaults(self):
        """Loading a missing file creates it with the defaults."""
        store = config_lib.ConfigStore(self.path)
        settings = store.load()
        self.assertTrue(os.path.isfile(self.path))
        self.assertEqual(settings.listen_port, 8443)
        with open(self.path, 'r', encoding='utf-8') as handle:
            self.assertEqual(json.load(handle)['listen_port'], 8443)

    def test_values_are_coerced(self):
        """Strings from a form become numbers, booleans and lists."""
        store = self._store()
        settings = store.update({
            'listen_port': '9443',
            'scheduler_enabled': 'no',
            'allowed_users': 'ada, grace lovelace',
            'session_minutes': 'not a number',
        })
        self.assertEqual(settings.listen_port, 9443)
        self.assertFalse(settings.scheduler_enabled)
        self.assertEqual(settings.allowed_users,
                         ['ada', 'grace', 'lovelace'])
        self.assertEqual(settings.session_minutes, 720)

    def test_unknown_keys_are_ignored(self):
        """Extra keys in the file do not upset loading."""
        jsonstore.write_json(self.path, {'listen_port': 1234,
                                         'colour': 'green'})
        settings = config_lib.ConfigStore(self.path).load()
        self.assertEqual(settings.listen_port, 1234)
        self.assertFalse(hasattr(settings, 'colour'))

    def test_secrets_are_kept_and_hidden(self):
        """An empty secret leaves the stored one alone, and is hidden."""
        store = self._store()
        store.update({'device_password': 'hunter2'})
        settings = store.update({'device_password': ''})
        self.assertEqual(settings.device_password, 'hunter2')
        redacted = settings.redacted()
        self.assertNotIn('device_password', redacted)
        self.assertTrue(redacted['device_password_set'])

    def test_derived_paths(self):
        """Device and TLS paths follow the state directory."""
        store = self._store()
        settings = store.config
        self.assertTrue(settings.devices_file.endswith('devices.json'))
        self.assertTrue(settings.device_dir.endswith('devices'))
        self.assertTrue(
            settings.resolved_certificate().endswith('panel.crt'))


class DeviceStoreTest(unittest.TestCase):
    """The inventory and the per-device files."""

    def setUp(self):
        """Creates an empty store in a scratch directory."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = devicestore.DeviceStore(
            os.path.join(self.tmp.name, 'devices.json'),
            os.path.join(self.tmp.name, 'devices'))
        self.store.load()

    def test_add_creates_inventory_and_device_file(self):
        """Adding a device writes both files."""
        record = self.store.add('192.168.1.42', alias='Kitchen lamp',
                                info={'mac': 'AA:BB:CC:00:11:22',
                                      'model': 'HS100'})
        self.assertTrue(devicestore.is_device_id(record.device_id),
                        'the identifier should be a random UUID')
        self.assertEqual(record.model, 'HS100')
        self.assertTrue(os.path.isfile(
            os.path.join(self.tmp.name, 'devices.json')))
        self.assertTrue(os.path.isfile(
            self.store.device_path(record.device_id)))

    def test_identifiers_are_random_and_say_nothing(self):
        """Two devices get unrelated identifiers, derived from nothing."""
        first = self.store.add('192.168.1.42', alias='Kitchen lamp',
                               info={'mac': 'AA:BB:CC:00:11:22'})
        second = self.store.add('plug.example', alias='Hall plug')
        self.assertNotEqual(first.device_id, second.device_id)
        for record in (first, second):
            self.assertTrue(devicestore.is_device_id(record.device_id))

    # A name on disk built from input is a name an attacker has a say
    # in, so nothing the device or the operator supplies may appear in
    # one.
    def test_nothing_about_the_device_reaches_the_file_name(self):
        """The file name carries no host, MAC, alias or model."""
        record = self.store.add(
            '192.168.1.42', alias='Kitchen lamp',
            info={'mac': 'AA:BB:CC:00:11:22', 'model': 'HS100',
                  'alias': 'Reported name'})
        name = os.path.basename(self.store.device_path(record.device_id))
        for secret in ('192.168.1.42', '19216814', 'aabbcc001122',
                       'kitchen', 'lamp', 'hs100', 'reported'):
            self.assertNotIn(secret, name.lower())
        digest = hashlib.sha256(b'192.168.1.42').hexdigest()[:12]
        self.assertNotIn(digest, name,
                         'not even a hash of the host may appear')

    def test_a_hostile_identifier_cannot_name_a_file(self):
        """An identifier that is not ours is refused, not scrubbed."""
        for nasty in ('../../etc/passwd', '..', '/etc/shadow', '',
                      'mac-aabbcc001122', 'a' * 200):
            with self.assertRaises(devicestore.DeviceError):
                self.store.device_path(nasty)

    def test_identity_survives_a_change_of_address(self):
        """A device that moves keeps its identifier and its schedule."""
        first = self.store.add('192.168.1.42', alias='Kitchen lamp',
                               info={'mac': 'AA:BB:CC:00:11:22'})
        self.store.set_schedule(first.device_id, '0 6 * * * on\n')
        again = self.store.add('192.168.1.99',
                               info={'mac': 'aa:bb:cc:00:11:22'})
        self.assertEqual(again.device_id, first.device_id)
        self.assertEqual(again.host, '192.168.1.99')
        self.assertEqual(len(self.store.records()), 1)
        self.assertIn('0 6 * * * on',
                      self.store.get_schedule(first.device_id))

    def test_an_entry_with_an_unusable_identifier_is_skipped(self):
        """A bad identifier is a bad entry, and does not stop the load."""
        jsonstore.write_json(
            os.path.join(self.tmp.name, 'devices.json'),
            {'version': 1, 'devices': [
                {'device_id': 'not-a-uuid', 'host': '10.0.0.5',
                 'alias': 'Broken'},
                {'device_id': '2f1c7a20-6d5b-4a1e-9f77-8c2f0b3d4e51',
                 'host': '10.0.0.6', 'alias': 'Fine'}]})
        store = devicestore.DeviceStore(
            os.path.join(self.tmp.name, 'devices.json'),
            os.path.join(self.tmp.name, 'devices'))
        records = store.load()
        self.assertEqual([record.alias for record in records], ['Fine'])

    def test_adding_the_same_host_twice_updates(self):
        """A second add updates the record instead of duplicating it."""
        self.store.add('192.168.1.42', alias='Lamp')
        self.store.add('192.168.1.42', info={'model': 'KP115'})
        records = self.store.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].alias, 'Lamp')
        self.assertEqual(records[0].model, 'KP115')

    def test_update_and_disable(self):
        """Editable fields change and disabled devices drop out."""
        record = self.store.add('192.168.1.42')
        self.store.update(record.device_id, {'alias': 'Fan',
                                             'enabled': False})
        self.assertEqual(self.store.get(record.device_id).alias, 'Fan')
        self.assertEqual(self.store.enabled_records(), [])

    def test_schedule_is_validated_before_saving(self):
        """A bad script is refused and the old one is kept."""
        record = self.store.add('192.168.1.42')
        self.store.set_schedule(record.device_id, '0 6 * * * on')
        with self.assertRaises(Exception):
            self.store.set_schedule(record.device_id, '0 6 * * * boom')
        self.assertEqual(self.store.get_schedule(record.device_id),
                         '0 6 * * * on')

    def test_remove_deletes_the_device_file(self):
        """Removing a device takes its schedule file with it."""
        record = self.store.add('192.168.1.42')
        path = self.store.device_path(record.device_id)
        self.store.remove(record.device_id)
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.store.records(), [])
        with self.assertRaises(devicestore.DeviceError):
            self.store.get(record.device_id)

    def test_reload_reads_what_was_written(self):
        """A fresh store sees the devices written by the first one."""
        self.store.add('192.168.1.42', alias='Lamp')
        again = devicestore.DeviceStore(
            os.path.join(self.tmp.name, 'devices.json'),
            os.path.join(self.tmp.name, 'devices'))
        self.assertEqual([record.alias for record in again.load()], ['Lamp'])

    def test_bad_entries_are_skipped(self):
        """An inventory entry without a host is ignored, not fatal."""
        jsonstore.write_json(
            os.path.join(self.tmp.name, 'devices.json'),
            {'version': 1, 'devices': [{'alias': 'nowhere'},
                                       {'host': '10.0.0.5'}]})
        records = self.store.load()
        self.assertEqual([record.host for record in records], ['10.0.0.5'])


if __name__ == '__main__':
    unittest.main()
