# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.userstore`."""

import os
import stat
import tempfile
import unittest

from kasapanel import jsonstore
from kasapanel import userstore

DEVICE_A = '3f7c9d1a-5b24-4e18-9c0f-2a6d8e4b7103'
DEVICE_B = '7c1e4b80-2d6a-4f13-8b05-91ad3e6c7f22'


class NameTest(unittest.TestCase):
    """Only an account name may name a file."""

    def test_ordinary_names_are_allowed(self):
        """The names people actually sign in with."""
        for name in ('ada', 'root', 'www-data', 'user_1', 'ada.lovelace',
                     '_service'):
            self.assertTrue(userstore.is_username(name), name)

    def test_anything_that_could_be_a_path_is_refused(self):
        """Refused rather than scrubbed: the point is that no string
        which is not an account name can name a file at all."""
        for name in ('', '.', '..', '../../etc/passwd', 'ada/../bob',
                     'ada\x00', 'has space', '.hidden', 'a' * 40,
                     '/etc/shadow'):
            self.assertFalse(userstore.is_username(name), repr(name))

    def test_a_refused_name_never_reaches_a_path(self):
        """The store asks before it builds the name, not after."""
        store = userstore.UserStore('/nowhere')
        with self.assertRaises(userstore.UserError):
            store.path_for('../config')
        with self.assertRaises(userstore.UserError):
            store.preferences('')


class ValueTest(unittest.TestCase):
    """Values that make no sense are dropped, not stored."""

    def test_the_theme_must_be_one_the_stylesheet_knows(self):
        """A theme nobody styled would leave the panel undressed."""
        self.assertEqual(userstore.clean_theme('dark'), 'dark')
        self.assertEqual(userstore.clean_theme('DARK'), 'dark')
        self.assertEqual(userstore.clean_theme(' light '), 'light')
        self.assertEqual(userstore.clean_theme('plaid'), 'light')
        self.assertEqual(userstore.clean_theme(None), 'light')
        self.assertEqual(userstore.clean_theme('plaid', 'dark'), 'dark',
                         'a value that cannot be used leaves the old one')

    def test_the_card_order_holds_device_identifiers_and_nothing_else(self):
        """It is read back into a payload, so it is checked going in."""
        self.assertEqual(
            userstore.clean_card_order([DEVICE_A, 'not-an-id', 42]),
            [DEVICE_A])
        self.assertEqual(userstore.clean_card_order('nonsense'), [])
        self.assertEqual(userstore.clean_card_order(None), [])

    def test_a_device_named_twice_is_named_once(self):
        """A card has one position, so a repeat says nothing."""
        self.assertEqual(
            userstore.clean_card_order([DEVICE_A, DEVICE_B, DEVICE_A]),
            [DEVICE_A, DEVICE_B])

    def test_a_long_order_is_cut_rather_than_kept(self):
        """A preference file is not a place to store a list of any size."""
        many = [DEVICE_A] + [f'{n:08x}-0000-4000-8000-000000000000'
                             for n in range(userstore.MAX_CARD_ORDER + 50)]
        self.assertEqual(len(userstore.clean_card_order(many)),
                         userstore.MAX_CARD_ORDER)


class StoreTest(unittest.TestCase):
    """Reading, writing and surviving a restart."""

    def setUp(self):
        """Builds a store in a temporary directory."""
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user_dir = os.path.join(self.tmp.name, 'users')
        self.store = userstore.UserStore(self.user_dir)

    def test_an_unknown_user_gets_the_defaults(self):
        """Signing in for the first time is not an error."""
        preferences = self.store.preferences('ada')
        self.assertEqual(preferences['theme'], 'light')
        self.assertEqual(preferences['device_card_order'], [])
        self.assertEqual(preferences['username'], 'ada')

    def test_reading_preferences_writes_nothing(self):
        """A file that only holds the defaults is a file for nothing."""
        self.store.preferences('ada')
        self.assertFalse(os.path.exists(os.path.join(self.user_dir,
                                                     'ada.json')))

    def test_a_choice_is_kept_in_the_users_directory(self):
        """One file per operator, named after the account."""
        self.store.update('ada', {'theme': 'dark'})
        path = os.path.join(self.user_dir, 'ada.json')
        self.assertTrue(os.path.exists(path))
        self.assertEqual(jsonstore.read_json(path)['theme'], 'dark')

    def test_a_choice_survives_a_restart(self):
        """A fresh store reads what the last one wrote."""
        self.store.update('ada', {'theme': 'dark',
                                  'device_card_order': [DEVICE_B, DEVICE_A]})
        again = userstore.UserStore(self.user_dir)
        preferences = again.preferences('ada')
        self.assertEqual(preferences['theme'], 'dark')
        self.assertEqual(preferences['device_card_order'],
                         [DEVICE_B, DEVICE_A])

    def test_two_operators_do_not_share(self):
        """The point of a per-user file."""
        self.store.update('ada', {'theme': 'dark'})
        self.store.update('bob', {'device_card_order': [DEVICE_A]})
        self.assertEqual(self.store.preferences('ada')['theme'], 'dark')
        self.assertEqual(self.store.preferences('ada')['device_card_order'],
                         [])
        self.assertEqual(self.store.preferences('bob')['theme'], 'light')
        self.assertEqual(self.store.preferences('bob')['device_card_order'],
                         [DEVICE_A])

    def test_one_change_leaves_the_other_alone(self):
        """Saving a theme must not forget where the cards are."""
        self.store.update('ada', {'device_card_order': [DEVICE_A, DEVICE_B]})
        self.store.update('ada', {'theme': 'dark'})
        preferences = self.store.preferences('ada')
        self.assertEqual(preferences['theme'], 'dark')
        self.assertEqual(preferences['device_card_order'],
                         [DEVICE_A, DEVICE_B])

    def test_a_field_nobody_knows_is_ignored(self):
        """An older panel must not be able to wipe a newer field."""
        self.store.update('ada', {'theme': 'dark', 'favourite': 'cheese'})
        self.assertNotIn('favourite', self.store.preferences('ada'))

    def test_the_file_is_only_readable_by_its_owner(self):
        """It is kept like every other file the daemon writes."""
        self.store.update('ada', {'theme': 'dark'})
        mode = stat.S_IMODE(
            os.stat(os.path.join(self.user_dir, 'ada.json')).st_mode)
        self.assertEqual(mode, jsonstore.FILE_MODE)

    def test_nonsense_in_the_file_is_corrected_on_the_way_out(self):
        """A hand edited file must not be able to break the panel."""
        jsonstore.ensure_directory(self.user_dir)
        jsonstore.write_json(os.path.join(self.user_dir, 'ada.json'), {
            'theme': 'plaid',
            'device_card_order': ['../secret', DEVICE_A],
        })
        preferences = self.store.preferences('ada')
        self.assertEqual(preferences['theme'], 'light')
        self.assertEqual(preferences['device_card_order'], [DEVICE_A])

    def test_a_file_that_is_not_json_loses_the_preference_not_the_panel(self):
        """Preferences are a convenience; nothing here may stop work."""
        jsonstore.ensure_directory(self.user_dir)
        with open(os.path.join(self.user_dir, 'ada.json'), 'w',
                  encoding='utf-8') as handle:
            handle.write('{this is not json')
        with self.assertLogs('kasapanel.userstore', level='WARNING'):
            preferences = self.store.preferences('ada')
        self.assertEqual(preferences['theme'], 'light')

    def test_a_file_holding_a_list_is_treated_as_no_file_at_all(self):
        """``read_json`` will happily return something that is not ours."""
        jsonstore.ensure_directory(self.user_dir)
        jsonstore.write_json(os.path.join(self.user_dir, 'ada.json'),
                             ['dark'])
        self.assertEqual(self.store.preferences('ada')['theme'], 'light')

    def test_an_order_naming_a_device_that_is_gone_is_kept(self):
        """A device removed by mistake should come back where it was."""
        self.store.update('ada', {'device_card_order': [DEVICE_A, DEVICE_B]})
        self.assertIn(DEVICE_B, self.store.preferences('ada')[
            'device_card_order'])


if __name__ == '__main__':
    unittest.main()
