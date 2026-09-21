# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Tests for :mod:`kasapanel.privsep`.

The parts that need root are exercised only when the tests are run as
root; the protocol and the account rules are tested either way.
"""

import ctypes
import os
import pwd
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from kasapanel import auth
from kasapanel import config
from kasapanel import privsep


def account(name: str, uid: int = 1000,
            gid: int = 1000) -> pwd.struct_passwd:
    """Builds a password database entry.

    Args:
        name: Account name.
        uid: User id.
        gid: Group id.

    Returns:
        The entry.
    """
    return pwd.struct_passwd(
        (name, 'x', uid, gid, '', f'/home/{name}', '/usr/sbin/nologin'))


class ResolveUserTest(unittest.TestCase):
    """Choosing the account to run as."""

    def test_a_configured_user_is_used(self):
        """What the operator asked for wins."""
        wanted = account('kasapanel')
        with mock.patch('pwd.getpwnam', return_value=wanted) as lookup:
            found, last_resort = privsep.resolve_user('kasapanel')
        self.assertEqual(found.pw_name, 'kasapanel')
        self.assertFalse(last_resort)
        lookup.assert_called_once_with('kasapanel')

    def test_a_missing_configured_user_is_fatal(self):
        """Silently running somewhere else would be worse than failing."""
        with mock.patch('pwd.getpwnam', side_effect=KeyError):
            with self.assertRaises(privsep.PrivilegeError) as caught:
                privsep.resolve_user('kasapanel')
        self.assertIn('does not exist', str(caught.exception))

    def test_falls_back_to_www_data(self):
        """With nothing configured, the usual service account is used."""
        def lookup(name):
            if name == 'www-data':
                return account('www-data', 33, 33)
            raise KeyError(name)

        with mock.patch('pwd.getpwnam', side_effect=lookup):
            found, last_resort = privsep.resolve_user('')
        self.assertEqual(found.pw_name, 'www-data')
        self.assertFalse(last_resort)

    def test_falls_back_to_httpd(self):
        """On systems that call it httpd instead."""
        def lookup(name):
            if name == 'httpd':
                return account('httpd', 48, 48)
            raise KeyError(name)

        with mock.patch('pwd.getpwnam', side_effect=lookup):
            found, last_resort = privsep.resolve_user('')
        self.assertEqual(found.pw_name, 'httpd')
        self.assertFalse(last_resort)

    def test_falls_back_to_nobody_and_says_so(self):
        """The last resort is flagged, because it needs fixing."""
        def lookup(name):
            if name == 'nobody':
                return account('nobody', 65534, 65534)
            raise KeyError(name)

        with mock.patch('pwd.getpwnam', side_effect=lookup):
            found, last_resort = privsep.resolve_user('')
        self.assertEqual(found.pw_name, 'nobody')
        self.assertTrue(last_resort)

    def test_no_account_at_all_is_fatal(self):
        """Without even nobody there is nothing safe to become."""
        with mock.patch('pwd.getpwnam', side_effect=KeyError):
            with self.assertRaises(privsep.PrivilegeError) as caught:
                privsep.resolve_user('')
        self.assertIn('nobody', str(caught.exception))

    def test_naming_nobody_explicitly_still_warns(self):
        """Configuring the last resort by hand does not excuse it."""
        with mock.patch('pwd.getpwnam',
                        return_value=account('nobody', 65534, 65534)):
            found, last_resort = privsep.resolve_user('nobody')
        self.assertEqual(found.pw_name, 'nobody')
        self.assertTrue(last_resort)

    def test_the_description_carries_the_warning(self):
        """The panel gets a sentence it can show as-is."""
        plain = privsep.describe_user(account('kasapanel'), False)
        self.assertEqual(plain['warning'], '')
        self.assertFalse(plain['last_resort'])
        shouted = privsep.describe_user(account('nobody', 65534), True)
        self.assertTrue(shouted['last_resort'])
        self.assertIn('nobody', shouted['warning'])
        self.assertIn('daemon_user', shouted['warning'])


class ProtocolTest(unittest.TestCase):
    """The two-message conversation with the helper."""

    def setUp(self):
        """Connects a fake helper to a client."""
        self.here, self.there = socket.socketpair()
        self.addCleanup(self.here.close)
        self.addCleanup(self.there.close)

    def test_a_round_trip(self):
        """A message survives the wire in both directions."""
        # pylint: disable=protected-access
        privsep._send(self.here, {'op': 'auth', 'username': 'ada'})
        self.assertEqual(privsep._receive(self.there)['username'], 'ada')

    def test_end_of_stream_is_none(self):
        """A closed peer reads as None rather than raising."""
        self.here.close()
        # pylint: disable=protected-access
        self.assertIsNone(privsep._receive(self.there))

    def test_an_oversized_message_is_refused(self):
        """A length header cannot be used to demand a huge read."""
        # pylint: disable=protected-access
        self.here.sendall(privsep._LENGTH.pack(privsep.MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(privsep.HelperError):
            privsep._receive(self.there)


class HelperConversationTest(unittest.TestCase):
    """The helper loop, driven directly rather than through a fork."""

    def serve(self, answers):
        """Runs the helper loop against a scripted PAM.

        Args:
            answers: Mapping of password to whether PAM accepts it.

        Returns:
            The client end of the socket.
        """
        here, there = socket.socketpair()

        class FakeSession:
            """Stands in for a python-pam authenticator."""

            def authenticate(self, username, password, service='login'):
                """Returns the scripted answer.

                Args:
                    username: Ignored.
                    password: Looked up in the script.
                    service: Ignored.

                Returns:
                    Whether the password is accepted.
                """
                del username, service
                return answers.get(password, False)

        def run():
            """Serves until the client hangs up."""
            with mock.patch.object(auth, 'new_authenticator',
                                   return_value=FakeSession()):
                # pylint: disable=protected-access
                privsep._serve_helper(there)
            there.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(here.close)
        return here

    def test_accepts_and_refuses(self):
        """The helper reports what PAM said, and nothing else."""
        client = self.serve({'right': True})
        helper = privsep.AuthHelper(client, os.getpid())
        self.assertTrue(helper.authenticate('ada', 'right', 'login'))
        self.assertFalse(helper.authenticate('ada', 'wrong', 'login'))

    def test_an_unsupported_request_is_refused(self):
        """The helper does exactly one thing and no more."""
        client = self.serve({})
        # pylint: disable=protected-access
        privsep._send(client, {'op': 'read-file', 'path': '/etc/shadow'})
        answer = privsep._receive(client)
        self.assertEqual(answer, {'error': 'unsupported request'})

    def test_a_pam_failure_does_not_kill_the_helper(self):
        """One bad request must not stop everyone signing in."""
        here, there = socket.socketpair()
        calls = []

        def flaky():
            """Fails once, then works."""
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError('PAM exploded')
            session = mock.Mock()
            session.authenticate.return_value = True
            return session

        def run():
            """Serves until the client hangs up."""
            with mock.patch.object(auth, 'new_authenticator',
                                   side_effect=flaky):
                # pylint: disable=protected-access
                privsep._serve_helper(there)
            there.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(here.close)

        helper = privsep.AuthHelper(here, os.getpid())
        with self.assertRaises(privsep.HelperError) as caught:
            helper.authenticate('ada', 'x', 'login')
        self.assertIn('PAM exploded', str(caught.exception))
        self.assertTrue(helper.authenticate('ada', 'x', 'login'))

    def test_a_dead_helper_is_reported_clearly(self):
        """The operator is told the daemon needs restarting."""
        here, there = socket.socketpair()
        helper = privsep.AuthHelper(here, os.getpid())
        there.close()
        with self.assertRaises(privsep.HelperError) as caught:
            helper.authenticate('ada', 'x', 'login')
        self.assertIn('restarted', str(caught.exception))


class AuthenticatorDelegationTest(unittest.TestCase):
    """The policy stays with the daemon; only the check is delegated."""

    def test_the_verifier_is_used_instead_of_local_pam(self):
        """A configured verifier replaces the in-process PAM call."""
        seen = []

        def verify(username, password, service):
            """Records the request and accepts.

            Args:
                username: Account name.
                password: The password.
                service: PAM service.

            Returns:
                True.
            """
            seen.append((username, password, service))
            return True

        authenticator = auth.Authenticator(
            config.PanelConfig(pam_service='kasapanel'), verifier=verify)
        self.assertEqual(authenticator.authenticate('ada', 'secret'), 'ada')
        self.assertEqual(seen, [('ada', 'secret', 'kasapanel')])

    def test_policy_is_still_applied_on_this_side(self):
        """The allow list and the lockout are not delegated."""
        authenticator = auth.Authenticator(
            config.PanelConfig(allowed_users=['grace'],
                               max_login_failures=2),
            verifier=lambda *args: True)
        with self.assertRaises(auth.AuthError) as caught:
            authenticator.authenticate('ada', 'secret')
        self.assertIn('not allowed', str(caught.exception))

        refusing = auth.Authenticator(
            config.PanelConfig(max_login_failures=2),
            verifier=lambda *args: False)
        for _ in range(2):
            with self.assertRaises(auth.AuthError):
                refusing.authenticate('ada', 'wrong')
        with self.assertRaises(auth.AuthError) as caught:
            refusing.authenticate('ada', 'wrong')
        self.assertIn('too many failed attempts',
                      str(caught.exception).lower())

    def test_a_helper_failure_reads_as_an_auth_error(self):
        """A broken helper does not leak its own exception type."""
        def explode(*args):
            """Fails the way a dead helper does.

            Args:
                *args: Ignored.

            Raises:
                auth.AuthError: Always.
            """
            del args
            raise auth.AuthError('the authentication helper stopped')

        authenticator = auth.Authenticator(config.PanelConfig(),
                                           verifier=explode)
        with self.assertRaises(auth.AuthError):
            authenticator.authenticate('ada', 'secret')


class ExitReportingTest(unittest.TestCase):
    """Noticing, and explaining, a helper that has gone."""

    def test_describes_a_signal(self):
        """A killed helper names the signal."""
        killed = privsep.describe_exit(signal.SIGKILL)
        self.assertIn('SIGKILL', killed)
        self.assertIn('out-of-memory', killed)
        self.assertIn('SIGTERM', privsep.describe_exit(signal.SIGTERM))

    def test_describes_an_exit_code(self):
        """A helper that exited names its status."""
        self.assertIn('status 3', privsep.describe_exit(3 << 8))
        self.assertIn('without being asked', privsep.describe_exit(0))

    def test_describes_a_vanished_process(self):
        """An unreadable status still produces a sentence."""
        self.assertIn('disappeared', privsep.describe_exit(None))

    def test_death_is_reported_to_the_daemon(self):
        """The callback fires as soon as the helper goes."""
        here, there = socket.socketpair()
        self.addCleanup(here.close)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            here.close()
            try:
                there.recv(1)
            finally:
                os._exit(3)  # pylint: disable=protected-access
        there.close()

        helper = privsep.AuthHelper(here, pid)
        seen = []
        done = threading.Event()
        helper.watch(lambda reason: (seen.append(reason), done.set()))
        self.assertTrue(helper.alive)

        os.kill(pid, signal.SIGKILL)
        self.assertTrue(done.wait(timeout=10), 'the death was not noticed')
        self.assertIn('SIGKILL', seen[0])
        self.assertFalse(helper.alive)

    def test_a_deliberate_shutdown_is_not_a_failure(self):
        """Closing the helper must not look like it died."""
        here, there = socket.socketpair()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            here.close()
            try:
                there.recv(1)
            finally:
                os._exit(0)  # pylint: disable=protected-access
        there.close()

        helper = privsep.AuthHelper(here, pid)
        seen = []
        helper.watch(seen.append)
        helper.close()
        self.assertEqual(seen, [])
        self.assertFalse(helper.alive)


class DirectoryPermissionTest(unittest.TestCase):
    """Whether the daemon account could remove a file it owns."""

    def setUp(self):
        """Makes a scratch directory."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.me = pwd.getpwuid(os.getuid())

    def test_our_own_directory_is_writable(self):
        """A directory we own and may write counts as writable."""
        os.chmod(self.tmp, 0o755)
        self.assertTrue(privsep.writable_by(self.tmp, self.me))

    def test_a_read_only_directory_is_not(self):
        """Owning the file inside would not be enough to remove it."""
        other = account('someone-else', self.me.pw_uid + 1,
                        self.me.pw_gid + 1)
        os.chmod(self.tmp, 0o755)
        self.assertFalse(privsep.writable_by(self.tmp, other))
        os.chmod(self.tmp, 0o777)
        self.assertTrue(privsep.writable_by(self.tmp, other))

    def test_a_missing_directory_is_not_writable(self):
        """Nothing there is not somewhere we can write."""
        self.assertFalse(
            privsep.writable_by(os.path.join(self.tmp, 'absent'), self.me))


class AllowedDirectoryTest(unittest.TestCase):
    """Handing over a directory, but only one on the list."""

    def setUp(self):
        """Makes a scratch directory to work in."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make(self, name: str) -> str:
        """Creates a directory.

        Args:
            name: Directory name.

        Returns:
            Its path.
        """
        path = os.path.join(self.tmp, name)
        os.makedirs(path)
        return path

    def test_a_listed_directory_is_taken(self):
        """The daemon's own directories are handed over."""
        path = self.make('state')
        with mock.patch('os.chown') as chown:
            self.assertTrue(privsep.own_directory_if_allowed(
                path, 1234, 5678, {path}))
        chown.assert_called_once_with(path, 1234, 5678)

    # The rule used to be "any directory called kasapanel", which also
    # matched a source checkout.  Changing the ownership of somebody's
    # working directory is not something a daemon should do quietly.
    def test_a_directory_merely_named_after_the_daemon_is_not_taken(self):
        """A checkout at /opt/kasapanel is not ours to give away."""
        path = self.make('kasapanel')
        with mock.patch('os.chown') as chown:
            self.assertFalse(privsep.own_directory_if_allowed(
                path, 1234, 5678, {self.make('elsewhere')}))
        chown.assert_not_called()

    def test_an_unlisted_directory_is_left_alone(self):
        """It could be /etc, and /etc is not ours either."""
        path = self.make('etc')
        with mock.patch('os.chown') as chown:
            self.assertFalse(privsep.own_directory_if_allowed(
                path, 1234, 5678, set()))
        chown.assert_not_called()

    def test_paths_are_compared_after_resolving(self):
        """A trailing slash or a symlink does not defeat the list."""
        path = self.make('state')
        link = os.path.join(self.tmp, 'link')
        os.symlink(path, link)
        with mock.patch('os.chown'):
            self.assertTrue(privsep.own_directory_if_allowed(
                link, 1234, 5678, {path + '/'}))

    def test_a_missing_directory_is_not_taken(self):
        """Nothing there is nothing to hand over."""
        absent = os.path.join(self.tmp, 'absent')
        with mock.patch('os.chown') as chown:
            self.assertFalse(privsep.own_directory_if_allowed(
                absent, 1234, 5678, {absent}))
        chown.assert_not_called()

    def test_a_file_is_not_a_directory(self):
        """Only directories are considered."""
        path = os.path.join(self.tmp, 'state')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('')
        with mock.patch('os.chown') as chown:
            self.assertFalse(privsep.own_directory_if_allowed(
                path, 1234, 5678, {path}))
        chown.assert_not_called()


class PrivilegedAccountTest(unittest.TestCase):
    """Refusing to run as an account that is not unprivileged."""

    def test_root_is_refused(self):
        """Accepting root would defeat the whole arrangement."""
        with mock.patch('pwd.getpwnam', return_value=account('root', 0, 0)):
            with self.assertRaises(privsep.PrivilegeError) as caught:
                privsep.resolve_user('root')
        message = str(caught.exception)
        self.assertIn('uid 0', message)
        self.assertIn('unprivileged', message)

    def test_an_account_in_the_root_group_is_refused(self):
        """A gid of 0 is privileged too."""
        with mock.patch('pwd.getpwnam',
                        return_value=account('odd', 1000, 0)):
            with self.assertRaises(privsep.PrivilegeError):
                privsep.resolve_user('odd')

    def test_it_is_refused_before_anything_is_chowned(self):
        """The refusal happens while the files are still untouched."""
        with mock.patch('pwd.getpwnam', return_value=account('root', 0, 0)):
            with mock.patch('os.chown') as chown:
                with self.assertRaises(privsep.PrivilegeError):
                    privsep.resolve_user('root')
        chown.assert_not_called()


class CapabilityTest(unittest.TestCase):
    """Telling a missing capability apart from a file permission."""

    def test_a_full_set_is_not_missing_anything(self):
        """Holding everything needed reports nothing."""
        every = sum(1 << bit
                    for bit, _ in privsep.NEEDED_CAPABILITIES.values())
        with mock.patch.object(privsep, 'effective_capabilities',
                               return_value=every):
            self.assertEqual(privsep.missing_capabilities(), [])

    def test_a_missing_capability_is_named(self):
        """Each absent capability is reported by name."""
        with mock.patch.object(privsep, 'effective_capabilities',
                               return_value=0):
            missing = privsep.missing_capabilities()
        self.assertIn('CAP_SETUID', missing)
        self.assertIn('CAP_CHOWN', missing)

    def test_the_advice_names_the_unit_directive(self):
        """EPERM from uid 0 looks like a file problem and is not one."""
        advice = privsep.capability_advice(['CAP_SETUID'])
        self.assertIn('uid 0', advice)
        self.assertIn('CapabilityBoundingSet', advice)
        self.assertIn('systemctl show', advice)

    def test_no_advice_when_nothing_is_missing(self):
        """A healthy process gets no lecture."""
        self.assertEqual(privsep.capability_advice([]), '')

    def test_the_check_refuses_before_anything_is_chowned(self):
        """The point of checking first is to change nothing."""
        with mock.patch('os.getuid', return_value=0), \
                mock.patch.object(privsep, 'effective_capabilities',
                                  return_value=0):
            with mock.patch('os.chown') as chown:
                with self.assertRaises(privsep.PrivilegeError) as caught:
                    privsep.check_can_drop()
            chown.assert_not_called()
        self.assertIn('CAP_SETUID', str(caught.exception))

    def test_an_unprivileged_process_is_not_checked(self):
        """There is nothing to drop, so nothing to complain about."""
        with mock.patch('os.getuid', return_value=1000):
            privsep.check_can_drop()

    def test_the_drop_failure_explains_itself(self):
        """A bare EPERM is not a useful thing to print."""
        with mock.patch('os.getuid', return_value=0), \
                mock.patch('os.initgroups'), \
                mock.patch('os.setgid'), \
                mock.patch('os.setuid',
                           side_effect=PermissionError(1, 'not permitted')), \
                mock.patch.object(privsep, 'effective_capabilities',
                                  return_value=0):
            with self.assertRaises(privsep.PrivilegeError) as caught:
                privsep.drop_privileges(account('www-data', 33, 33))
        message = str(caught.exception)
        self.assertIn('cannot become www-data', message)
        self.assertIn('CapabilityBoundingSet', message)


class RefusalAdviceTest(unittest.TestCase):
    """Telling apart the reasons a drop can be refused."""

    def setUp(self):
        """Takes a real report to vary from."""
        self.base = privsep.privilege_report()
        self.account = account('www-data', 33, 33)

    def advise(self, missing, **overrides) -> str:
        """Builds the advice for a made-up situation.

        Args:
            missing: Capabilities that are not held.
            **overrides: Fields to change in the privilege report.

        Returns:
            The advice sentence.
        """
        report = dict(self.base)
        report.update(overrides)
        with mock.patch.object(privsep, 'missing_capabilities',
                               return_value=missing), \
                mock.patch.object(privsep, 'privilege_report',
                                  return_value=report):
            return privsep.refusal_advice(self.account)

    def test_a_missing_capability_points_at_the_bounding_set(self):
        """The commonest cause names the directive to change."""
        advice = self.advise(['CAP_SETUID'])
        self.assertIn('CapabilityBoundingSet', advice)

    def test_a_user_namespace_points_at_dynamicuser(self):
        """DynamicUser and PrivateUsers cannot work with this daemon."""
        advice = self.advise([], user_namespace=True)
        self.assertIn('user namespace', advice)
        self.assertIn('DynamicUser', advice)
        self.assertNotIn('CapabilityBoundingSet', advice)

    def test_seccomp_points_at_the_syscall_filter(self):
        """Capabilities held plus a filter in force reads differently."""
        advice = self.advise([], seccomp='2', user_namespace=False)
        self.assertIn('seccomp', advice)
        self.assertIn('SystemCallFilter', advice)

    def test_otherwise_it_says_to_look_outside_the_daemon(self):
        """When nothing here explains it, say so rather than guess."""
        advice = self.advise([], seccomp='0', user_namespace=False)
        self.assertIn('AppArmor', advice)
        self.assertIn('systemctl cat', advice)

    def test_the_state_is_always_included(self):
        """Every message carries the evidence behind it."""
        for advice in (self.advise(['CAP_SETUID']),
                       self.advise([], user_namespace=True),
                       self.advise([], seccomp='2'),
                       self.advise([])):
            self.assertIn('CapEff=', advice)
            self.assertIn('userns=', advice)

    def test_the_report_reads_this_process(self):
        """The report is real, not a guess."""
        report = privsep.privilege_report()
        self.assertEqual(report['uid'], os.getuid())
        self.assertTrue(report['cap_effective'])
        self.assertIn('CAP_SETUID=', privsep.capability_report())


class RaiseCapabilityTest(unittest.TestCase):
    """Taking a capability that is permitted but not effective."""

    @unittest.skipUnless(os.getuid() == 0, 'needs root')
    def test_a_permitted_capability_can_be_taken(self):
        """This is the state a service manager can leave behind."""
        # Do it in a child: the point is to lower a capability, and the
        # test process needs to keep its own.
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            os.close(read)
            result = b'?'
            try:
                # pylint: disable=protected-access
                libc = ctypes.CDLL(None, use_errno=True)
                header = privsep._CapHeader(privsep._CAP_VERSION_3, 0)
                data = (privsep._CapData * 2)()
                libc.capget(ctypes.byref(header), ctypes.byref(data))
                data[0].effective &= ~(1 << 7)  # lower CAP_SETUID
                libc.capset(ctypes.byref(header), ctypes.byref(data))
                lowered = privsep.missing_capabilities() == ['CAP_SETUID']
                raised = privsep.raise_effective(['CAP_SETUID'])
                recovered = not privsep.missing_capabilities()
                result = b'y' if (lowered and raised and recovered) else b'n'
            finally:
                os.write(write, result)
                os._exit(0)  # pylint: disable=protected-access
        os.close(write)
        answer = os.read(read, 1)
        os.close(read)
        os.waitpid(pid, 0)
        self.assertEqual(answer, b'y')

    def test_raising_what_is_not_permitted_does_nothing(self):
        """A capability the process was never given cannot be taken."""
        self.assertEqual(privsep.raise_effective([]), [])

    @unittest.skipUnless(os.getuid() == 0, 'needs root')
    def test_nothing_is_held_after_the_drop(self):
        """Dropping must leave no capability behind, ambient included."""
        read, write = os.pipe()
        pid = os.fork()
        if pid == 0:  # pragma: no cover - the child never returns
            os.close(read)
            try:
                privsep.drop_privileges(pwd.getpwnam('www-data'))
                held = privsep.effective_capabilities()
                os.write(write, b'y' if held == 0 else b'n')
            except Exception:  # pylint: disable=broad-except
                os.write(write, b'n')
            finally:
                os._exit(0)  # pylint: disable=protected-access
        os.close(write)
        answer = os.read(read, 1)
        os.close(read)
        os.waitpid(pid, 0)
        self.assertEqual(answer, b'y')


class AccessAsTest(unittest.TestCase):
    """Asking the kernel rather than reading the mode bits."""

    def setUp(self):
        """Makes a scratch directory and finds a service account."""
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        os.chmod(self.tmp, 0o755)
        for name in ('www-data', 'nobody'):
            try:
                self.other = pwd.getpwnam(name)
                break
            except KeyError:
                continue
        else:  # pragma: no cover
            self.skipTest('no unprivileged account to test with')

    def make(self, name: str, mode: int) -> str:
        """Writes a file with a given mode, owned by root.

        Args:
            name: File name.
            mode: Permission bits.

        Returns:
            The path.
        """
        path = os.path.join(self.tmp, name)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('x')
        os.chmod(path, mode)
        return path

    @unittest.skipUnless(os.getuid() == 0, 'needs root to become another')
    def test_a_private_file_is_not_readable(self):
        """The obvious case still works."""
        path = self.make('secret', 0o600)
        self.assertFalse(privsep.readable_by(path, self.other))

    @unittest.skipUnless(os.getuid() == 0, 'needs root to become another')
    def test_a_world_readable_file_is_readable(self):
        """So does the other obvious case."""
        path = self.make('open', 0o644)
        self.assertTrue(privsep.readable_by(path, self.other))

    # Certificates are usually distributed by giving a service account
    # an ACL on a key that stays 0600 root:root.  Reading st_mode calls
    # that unreadable, which is a false alarm about a real deployment.
    @unittest.skipUnless(os.getuid() == 0, 'needs root to set an ACL')
    def test_an_acl_grant_is_seen(self):
        """A key readable only through an ACL is readable."""
        if not shutil.which('setfacl'):
            self.skipTest('setfacl is not installed')
        path = self.make('privkey.pem', 0o600)
        result = subprocess.run(
            ['setfacl', '-m', f'user:{self.other.pw_name}:r', path],
            check=False, capture_output=True)
        if result.returncode != 0:
            self.skipTest('this filesystem does not support ACLs')
        self.assertFalse(
            # pylint: disable=protected-access
            privsep._mode_access(path, os.R_OK, self.other),
            'the mode bits alone should not explain this')
        self.assertTrue(privsep.readable_by(path, self.other),
                        'but the account can read it, so the check must '
                        'say so')

    @unittest.skipUnless(os.getuid() == 0, 'needs root to become another')
    def test_directory_writability_is_asked_the_same_way(self):
        """Directories go through the same path."""
        private = os.path.join(self.tmp, 'private')
        os.makedirs(private, mode=0o700)
        self.assertFalse(privsep.writable_by(private, self.other))
        os.chmod(private, 0o777)
        self.assertTrue(privsep.writable_by(private, self.other))

    def test_asking_about_this_process_needs_no_child(self):
        """No account, or already that account, is a plain check."""
        path = self.make('mine', 0o600)
        self.assertTrue(privsep.access_as(None, path, os.R_OK))


class UnprivilegedTest(unittest.TestCase):
    """What happens when there is no privilege to separate."""

    def test_no_helper_is_forked_when_not_root(self):
        """An unprivileged daemon keeps using PAM in process."""
        with mock.patch('os.getuid', return_value=1000):
            self.assertIsNone(privsep.start_helper())

    def test_dropping_is_a_no_op_when_not_root(self):
        """Nothing to drop, so nothing happens."""
        with mock.patch('os.getuid', return_value=1000):
            privsep.drop_privileges(account('kasapanel'))


@unittest.skipUnless(os.getuid() == 0, 'needs root')
class RootTest(unittest.TestCase):
    """The parts that only mean something as root."""

    def test_the_helper_survives_the_drop(self):
        """A forked helper answers after the daemon has dropped."""
        helper = privsep.start_helper()
        self.assertIsNotNone(helper)
        self.addCleanup(helper.close)
        self.assertTrue(helper.alive)
        # The helper is a separate process, so it still has root.
        self.assertNotEqual(helper.pid, os.getpid())

    def test_readable_by_sees_an_unreadable_key(self):
        """A root-only key is reported as unreadable by the account."""
        with tempfile.NamedTemporaryFile(delete=False) as handle:
            path = handle.name
        self.addCleanup(os.unlink, path)
        os.chmod(path, 0o600)
        os.chown(path, 0, 0)
        try:
            nobody = pwd.getpwnam('nobody')
        except KeyError:
            self.skipTest('no nobody account')
        self.assertFalse(privsep.readable_by(path, nobody))
        os.chmod(path, 0o644)
        self.assertTrue(privsep.readable_by(path, nobody))


if __name__ == '__main__':
    unittest.main()
