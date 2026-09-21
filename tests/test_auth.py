# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.auth`.

The PAM cases matter because a python-pam that is installed but not
importable used to be reported as a missing package, which sent the
operator looking in the wrong place.
"""

import sys
import unittest
from unittest import mock

from kasapanel import auth
from kasapanel import config


class FakePamSession:
    """A python-pam stand-in that answers from a fixed table."""

    def __init__(self, accept=True, blow_up=False):
        """Sets up the answer this session will give.

        Args:
            accept: Whether authentication should succeed.
            blow_up: Whether to raise instead of answering.
        """
        self.accept = accept
        self.blow_up = blow_up
        self.reason = 'test'
        self.calls = []

    def authenticate(self, username: str, password: str,
                     service: str = 'login') -> bool:
        """Records the attempt and gives the canned answer.

        Args:
            username: Account name.
            password: The password as typed.
            service: PAM service name.

        Returns:
            Whether the attempt is accepted.

        Raises:
            RuntimeError: If this session was built to fail.
        """
        self.calls.append((username, password, service))
        if self.blow_up:
            raise RuntimeError('no such PAM service')
        return self.accept


class PamDiagnosisTest(unittest.TestCase):
    """What the daemon says when PAM cannot be used."""

    def test_no_reason_when_pam_works(self):
        """A working import produces no complaint."""
        with mock.patch.object(auth, 'PAM_AVAILABLE', True):
            self.assertEqual(auth.pam_reason(), '')
            self.assertEqual(auth.pam_advice(), '')

    def test_missing_six_is_named(self):
        """The undeclared six dependency is called out by name."""
        with mock.patch.object(auth, 'PAM_AVAILABLE', False), \
                mock.patch.object(auth, '_IMPORT_ERROR',
                                  "No module named 'six'"):
            reason = auth.pam_reason()
            self.assertIn('six', reason)
            self.assertIn('installed but cannot be imported', reason)
            self.assertIn('pip install six', auth.pam_advice())

    def test_missing_package_mentions_the_interpreter(self):
        """A genuinely absent package points at the interpreter."""
        with mock.patch.object(auth, 'PAM_AVAILABLE', False), \
                mock.patch.object(auth, '_IMPORT_ERROR',
                                  "No module named 'pam'"):
            self.assertIn('interpreter', auth.pam_reason())
            self.assertIn(sys.executable, auth.pam_advice())

    def test_other_errors_are_passed_through(self):
        """An unexpected failure is reported verbatim."""
        with mock.patch.object(auth, 'PAM_AVAILABLE', False), \
                mock.patch.object(auth, '_IMPORT_ERROR',
                                  'OSError: libpam.so.0 is broken'):
            self.assertIn('libpam.so.0 is broken', auth.pam_reason())

    def test_diagnosis_reports_the_environment(self):
        """The diagnosis carries what an operator needs."""
        report = auth.pam_diagnosis()
        self.assertEqual(report['interpreter'], sys.executable)
        for key in ('available', 'reason', 'advice', 'module', 'libpam'):
            self.assertIn(key, report)

    def test_authenticator_refuses_without_pam(self):
        """Building an authenticator without PAM is an auth error."""
        with mock.patch.object(auth, 'PAM_AVAILABLE', False), \
                mock.patch.object(auth, '_IMPORT_ERROR',
                                  "No module named 'six'"):
            with self.assertRaises(auth.AuthError) as caught:
                auth.new_authenticator()
            self.assertIn('six', str(caught.exception))

    def test_both_python_pam_api_names_work(self):
        """Either the old or the new class name is accepted."""
        modern = mock.Mock(spec=['PamAuthenticator'])
        modern.PamAuthenticator.return_value = 'built'
        with mock.patch.object(auth, 'PAM_AVAILABLE', True), \
                mock.patch.object(auth, 'pam_module', modern):
            self.assertEqual(auth.new_authenticator(), 'built')

    def test_unloadable_libpam_is_explained(self):
        """A libpam that will not load reads as a server fault."""
        broken = mock.Mock(spec=['pam'])
        broken.pam.side_effect = OSError('cannot open shared object file')
        with mock.patch.object(auth, 'PAM_AVAILABLE', True), \
                mock.patch.object(auth, 'pam_module', broken):
            with self.assertRaises(auth.AuthError) as caught:
                auth.new_authenticator()
            self.assertIn('libpam', str(caught.exception))


class AuthenticatorTest(unittest.TestCase):
    """Sign-in policy on top of PAM."""

    def build(self, **overrides) -> auth.Authenticator:
        """Builds an authenticator with the given settings.

        Args:
            **overrides: Fields to override on the default config.

        Returns:
            The authenticator.
        """
        return auth.Authenticator(config.PanelConfig(**overrides))

    def test_empty_credentials_are_refused_before_pam(self):
        """A blank form never reaches PAM."""
        authenticator = self.build()
        with self.assertRaises(auth.AuthError):
            authenticator.authenticate('', '')

    def test_missing_pam_is_reported_at_sign_in(self):
        """The sign-in error names the real cause."""
        authenticator = self.build()
        with mock.patch.object(auth, 'PAM_AVAILABLE', False), \
                mock.patch.object(auth, '_IMPORT_ERROR',
                                  "No module named 'six'"):
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', 'secret')
            self.assertIn('six', str(caught.exception))

    def test_accepted_sign_in_uses_the_configured_service(self):
        """The configured PAM service is the one consulted."""
        authenticator = self.build(pam_service='kasapanel')
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            self.assertEqual(authenticator.authenticate('ada', 'secret'),
                             'ada')
        self.assertEqual(session.calls[0][2], 'kasapanel')

    def test_pam_failure_is_a_server_fault(self):
        """A broken service is not reported as a wrong password."""
        authenticator = self.build(pam_service='nosuch')
        session = FakePamSession(blow_up=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', 'secret')
        message = str(caught.exception)
        self.assertIn('nosuch', message)
        self.assertNotIn('did not match', message)

    def test_allowed_users_is_enforced(self):
        """An account outside the list is refused after PAM accepts."""
        authenticator = self.build(allowed_users=['grace'])
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', 'secret')
            self.assertIn('not allowed', str(caught.exception))
            self.assertEqual(authenticator.authenticate('grace', 'secret'),
                             'grace')

    def test_lockout_after_repeated_failures(self):
        """Wrong passwords lock the account for a while."""
        authenticator = self.build(max_login_failures=2,
                                   lockout_seconds=300)
        session = FakePamSession(accept=False)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            for _ in range(2):
                with self.assertRaises(auth.AuthError):
                    authenticator.authenticate('ada', 'wrong')
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', 'wrong')
        self.assertIn('too many failed attempts',
                      str(caught.exception).lower())
        self.assertEqual(len(session.calls), 2)

    def test_success_clears_the_failure_count(self):
        """A good password forgives earlier mistakes."""
        authenticator = self.build(max_login_failures=2)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=FakePamSession(accept=False)):
            with self.assertRaises(auth.AuthError):
                authenticator.authenticate('ada', 'wrong')
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=FakePamSession(accept=True)):
            self.assertEqual(authenticator.authenticate('ada', 'right'),
                             'ada')
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=FakePamSession(accept=False)):
            for _ in range(2):
                with self.assertRaises(auth.AuthError):
                    authenticator.authenticate('ada', 'wrong')
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', 'wrong')
        self.assertIn('too many failed attempts',
                      str(caught.exception).lower())


if __name__ == '__main__':
    unittest.main()
