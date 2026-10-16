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


class RateLimitTest(unittest.TestCase):
    """The global limit on how often PAM may be engaged."""

    def test_one_a_second_by_default(self):
        """The first check goes through; the next one waits."""
        limit = auth.RateLimit()
        self.assertEqual(limit.interval,
                         auth.DEFAULT_PAM_RATE_LIMIT_SECONDS)
        limit.take(now=100.0)
        with self.assertRaises(auth.AuthError) as caught:
            limit.take(now=100.5)
        self.assertIn('0.5 seconds', str(caught.exception))

    def test_the_slot_comes_back(self):
        """Once the interval has passed another check is allowed."""
        limit = auth.RateLimit(1.0)
        limit.take(now=100.0)
        limit.take(now=101.0)
        limit.take(now=102.5)

    def test_a_refusal_does_not_take_the_slot(self):
        """Otherwise a caller in a loop would hold it for ever."""
        limit = auth.RateLimit(1.0)
        limit.take(now=100.0)
        for moment in (100.1, 100.2, 100.3):
            with self.assertRaises(auth.AuthError):
                limit.take(now=moment)
        limit.take(now=101.0)

    def test_zero_turns_it_off(self):
        """An operator who wants no limit can have none."""
        limit = auth.RateLimit(0)
        for moment in (100.0, 100.0, 100.0):
            limit.take(now=moment)


class EmptyPasswordTest(unittest.TestCase):
    """Nothing empty is worth asking PAM about."""

    def test_an_empty_password_is_refused(self):
        """The guard the daemon and the helper both call."""
        with self.assertRaises(auth.AuthError):
            auth.refuse_empty_password('')

    def test_a_password_is_left_alone(self):
        """Anything else passes through without comment."""
        self.assertIsNone(auth.refuse_empty_password('secret'))


class AuthenticatorTest(unittest.TestCase):
    """Sign-in policy on top of PAM."""

    def setUp(self):
        """Leaves the global rate limit as it was found."""
        self.addCleanup(auth.PAM_RATE_LIMIT.set_interval,
                        auth.DEFAULT_PAM_RATE_LIMIT_SECONDS)
        self.addCleanup(auth.PAM_RATE_LIMIT.reset)

    def build(self, **overrides) -> auth.Authenticator:
        """Builds an authenticator with the given settings.

        The rate limit is off unless a test asks for it: these tests
        make several attempts in a row, which is exactly what the limit
        exists to stop, and none of them is about the limit.

        Args:
            **overrides: Fields to override on the default config.

        Returns:
            The authenticator.
        """
        overrides.setdefault('pam_rate_limit_seconds', 0)
        auth.PAM_RATE_LIMIT.reset()
        return auth.Authenticator(config.PanelConfig(**overrides))

    def test_empty_credentials_are_refused_before_pam(self):
        """A blank form never reaches PAM."""
        authenticator = self.build()
        with self.assertRaises(auth.AuthError):
            authenticator.authenticate('', '')

    def test_an_empty_password_never_reaches_pam(self):
        """A named account with no password is refused on this side."""
        authenticator = self.build()
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.authenticate('ada', '')
        self.assertIn('password', str(caught.exception))
        self.assertEqual(session.calls, [],
                         'PAM must not be asked about an empty password')

    def test_an_empty_password_costs_nobody_their_turn(self):
        """Refusing one must not consume the rate limited slot."""
        authenticator = self.build(pam_rate_limit_seconds=1)
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            for _ in range(3):
                with self.assertRaises(auth.AuthError):
                    authenticator.authenticate('ada', '')
            self.assertEqual(authenticator.authenticate('ada', 'secret'),
                             'ada')

    def test_confirming_checks_the_password_again(self):
        """A locked device's confirmation goes through the same PAM."""
        authenticator = self.build(pam_service='kasapanel')
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            self.assertEqual(authenticator.confirm('ada', 'secret'), 'ada')
        self.assertEqual(session.calls, [('ada', 'secret', 'kasapanel')])

    def test_a_bad_confirmation_is_refused(self):
        """A wrong password confirms nothing."""
        authenticator = self.build()
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=FakePamSession(accept=False)):
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.confirm('ada', 'wrong')
        self.assertIn('did not match', str(caught.exception))

    def test_confirming_obeys_the_rate_limit(self):
        """One confirmation a second, like every other PAM check."""
        authenticator = self.build(pam_rate_limit_seconds=1)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=FakePamSession(accept=True)):
            self.assertEqual(authenticator.confirm('ada', 'secret'), 'ada')
            with self.assertRaises(auth.AuthError) as caught:
                authenticator.confirm('ada', 'secret')
        self.assertIn('try again', str(caught.exception))

    def test_the_rate_limit_is_global_not_per_account(self):
        """A list of names must not buy a list of attempts."""
        authenticator = self.build(pam_rate_limit_seconds=1)
        session = FakePamSession(accept=True)
        with mock.patch.object(auth, 'new_authenticator',
                               return_value=session):
            self.assertEqual(authenticator.authenticate('ada', 'secret'),
                             'ada')
            with self.assertRaises(auth.AuthError):
                authenticator.authenticate('grace', 'secret')
            with self.assertRaises(auth.AuthError):
                authenticator.authenticate('turing', 'secret')
        self.assertEqual(len(session.calls), 1)

    def test_the_rate_limit_is_configurable(self):
        """The gap between checks comes from the configuration."""
        self.build(pam_rate_limit_seconds=5)
        self.assertEqual(auth.PAM_RATE_LIMIT.interval, 5.0)
        self.build(pam_rate_limit_seconds=0)
        self.assertEqual(auth.PAM_RATE_LIMIT.interval, 0.0)

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
