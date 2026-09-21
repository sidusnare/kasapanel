# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""The command line entry point.

``kasapanel run`` is the daemon itself.  The other commands exist so an
operator can check a configuration, look at the resolved paths or renew
the self-signed certificate without starting anything.

The process can detach with ``--daemonize``, but running in the
foreground under systemd is the better arrangement: systemd then owns
restarts, logging and the shutdown signal.
"""

import argparse
import json
import logging
import os
import pwd
import signal
import sys
import threading
from typing import Any, Callable, List, Optional

from kasapanel import APP_NAME
from kasapanel import __version__
from kasapanel import app as app_lib
from kasapanel import auth
from kasapanel import certs
from kasapanel import certwatch
from kasapanel import config as config_lib
from kasapanel import devicestore
from kasapanel import filecheck
from kasapanel import httpd
from kasapanel import jsonstore
from kasapanel import privsep
from kasapanel import schedule as schedule_lib

_LOG = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1


def build_parser() -> argparse.ArgumentParser:
    """Builds the command line parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog='kasapanel',
        description='HTTPS dashboard and scheduler for Kasa devices.')
    parser.add_argument('--version', action='version',
                        version=f'kasapanel {__version__}')
    default_config = config_lib.default_config_path()
    parser.add_argument(
        '-c', '--config', default='',
        help=f'configuration file (default: {default_config})')
    parser.add_argument('--log-level', default='',
                        help='override the configured log level')
    subparsers = parser.add_subparsers(dest='command')

    run_parser = subparsers.add_parser('run', help='run the daemon')
    run_parser.add_argument('--host', default='',
                            help='override the listen address')
    run_parser.add_argument('--port', type=int, default=0,
                            help='override the listen port')
    run_parser.add_argument('--daemonize', action='store_true',
                            help='detach from the terminal')
    run_parser.add_argument('--pidfile', default='',
                            help='write the process id to this file')

    subparsers.add_parser('check',
                          help='check the configuration and schedules')
    subparsers.add_parser('paths', help='print the resolved file paths')

    cert_parser = subparsers.add_parser(
        'gen-cert', help='create the self-signed TLS certificate')
    cert_parser.add_argument('--force', action='store_true',
                            help='replace an existing certificate')
    cert_parser.add_argument('--days', type=int, default=certs.DEFAULT_DAYS,
                            help='how long the certificate stays valid')
    return parser


def _prepare(options: argparse.Namespace) -> app_lib.Application:
    """Builds an application and loads its configuration.

    Args:
        options: Parsed command line options.

    Returns:
        The loaded application.
    """
    application = app_lib.Application(options.config)
    application.load()
    if options.log_level:
        application.settings.log_level = options.log_level
        app_lib.configure_logging(application.settings)
    return application


def _write_pidfile(pidfile: str) -> str:
    """Writes the pid file, creating its directory if it is missing.

    Args:
        pidfile: File to write the process id to; may be empty.

    Returns:
        The directory this call created, or an empty string when it
        already existed.  The caller hands a directory it created to the
        daemon account, so the daemon can remove its own pid file again.

    Raises:
        OSError: If the file cannot be written.
    """
    if not pidfile:
        return ''
    created = ''
    directory = os.path.dirname(os.path.abspath(pidfile))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, mode=0o755, exist_ok=True)
        created = directory
    with open(pidfile, 'w', encoding='utf-8') as handle:
        handle.write(f'{os.getpid()}\n')
    return created


def _daemonize(pidfile: str) -> str:
    """Detaches the process from the terminal.

    Args:
        pidfile: File to write the process id to; may be empty.

    Returns:
        A pid file directory this call created, or an empty string.

    Raises:
        OSError: If forking fails.
    """
    if os.fork() > 0:
        os._exit(EXIT_OK)  # pylint: disable=protected-access
    os.setsid()
    if os.fork() > 0:
        os._exit(EXIT_OK)  # pylint: disable=protected-access
    os.chdir('/')
    with open(os.devnull, 'rb', 0) as null_in:
        os.dup2(null_in.fileno(), sys.stdin.fileno())
    with open(os.devnull, 'ab', 0) as null_out:
        os.dup2(null_out.fileno(), sys.stdout.fileno())
        os.dup2(null_out.fileno(), sys.stderr.fileno())
    return _write_pidfile(pidfile)


def _remove_pidfile(path: str) -> None:
    """Removes the pid file, tolerating a directory we cannot write.

    The file itself is handed to the daemon account at start up, but
    unlinking it needs write permission on the directory holding it,
    which an unprivileged daemon often does not have.  A leftover pid
    file is untidy; crashing on the way out over one is worse.

    Args:
        path: The pid file, if there is one.
    """
    if not path or not os.path.exists(path):
        return
    try:
        os.unlink(path)
    except OSError as error:
        _LOG.warning('cannot remove the pid file %s: %s', path, error)


def _helper_verifier(helper: privsep.AuthHelper) -> Callable:
    """Wraps the helper so the authenticator can call it.

    Args:
        helper: The daemon's end of the helper connection.

    Returns:
        A callable taking a username, a password and a service name.
    """
    def verify(username: str, password: str, service: str) -> bool:
        """Asks the privileged helper to check one password.

        Args:
            username: Account name.
            password: The password as typed.
            service: PAM service name.

        Returns:
            Whether PAM accepted it.

        Raises:
            auth.AuthError: If the helper cannot answer.
        """
        try:
            return helper.authenticate(username, password, service)
        except privsep.HelperError as error:
            raise auth.AuthError(str(error)) from error
    return verify


def _hand_over_directories(application: Any, settings: Any,
                           account: Any, created: str = '') -> None:
    """Gives the daemon account the directories it must write in.

    Saving the configuration and reopening the log file both write into
    a directory, not only to a file, because an atomic write needs to
    create a temporary file beside its target.  Directories named after
    the daemon are handed over; anything else is left alone and
    reported, because it might be /etc.

    Args:
        application: The application, for its configuration path.
        settings: The configuration in effect.
        account: The account the daemon is about to become.
    """
    # The directories the daemon may take ownership of are listed, not
    # guessed: its own state tree, the standard configuration and log
    # directories, and any directory it created itself this start.  A
    # configuration file kept somewhere else -- a source checkout, a
    # home directory -- is left alone and reported instead.
    allowed = {
        settings.state_dir,
        settings.device_dir,
        settings.tls_dir,
        os.path.dirname(config_lib.default_config_path()),
        os.path.join('/var/log', APP_NAME),
        created or '',
    }
    checks = (
        (os.path.dirname(os.path.abspath(application.config_store.path)),
         'settings saved from the panel will fail'),
    )
    if settings.log_file:
        checks += ((os.path.dirname(os.path.abspath(settings.log_file)),
                    'the log file cannot be recreated after rotation'),)
    for directory, consequence in checks:
        privsep.own_directory_if_allowed(
            directory, account.pw_uid, account.pw_gid, allowed)
        if not privsep.writable_by(directory, account):
            _LOG.warning(
                '%s is not writable by %s, so %s; give that directory to '
                'the daemon account, or move the file into a directory '
                'of its own', directory, account.pw_name, consequence)


def _warn_about_the_pidfile(pidfile: str, account: Any) -> None:
    """Warns when the pid file will not be removable on the way out.

    The file itself is handed to the daemon account, but unlinking it
    needs write permission on the directory holding it.  A pid file in a
    directory the daemon does not own -- /run, say -- will be left
    behind at shutdown, and it is better to say so at start up than to
    complain about it hours later.

    Args:
        pidfile: The pid file, if there is one.
        account: The account the daemon is about to become.
    """
    if not pidfile:
        return
    directory = os.path.dirname(os.path.abspath(pidfile))
    if not privsep.writable_by(directory, account):
        _LOG.warning(
            '%s is not writable by %s, so the pid file will be left '
            'behind when the daemon stops; put the pid file in a '
            'directory of its own, such as /run/kasapanel',
            directory, account.pw_name)


def _warn_about_unreadable_tls(settings: Any,
                               account: Any) -> None:
    """Warns when the daemon account cannot read the TLS material.

    The certificate watcher reloads a renewed certificate after
    privileges have been dropped, so the files have to stay readable by
    the unprivileged account.  A key under /etc/letsencrypt is not.

    Args:
        settings: The configuration in effect.
        account: The account the daemon is about to become.
    """
    for path in (settings.resolved_certificate(), settings.resolved_key()):
        if path and not privsep.readable_by(path, account):
            _LOG.warning(
                '%s will not be readable by %s once privileges are '
                'dropped; a renewed certificate cannot be loaded until '
                'the file is readable by that account',
                path, account.pw_name)


def command_run(options: argparse.Namespace) -> int:
    """Runs the daemon until it is asked to stop.

    Args:
        options: Parsed command line options.

    Returns:
        A process exit status.
    """
    application = _prepare(options)
    settings = application.settings
    if options.host:
        settings.listen_host = options.host
    if options.port:
        settings.listen_port = options.port
    try:
        certs.ensure_certificate(settings.resolved_certificate(),
                                 settings.resolved_key())
    except certs.CertificateError as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR
    if options.daemonize:
        pid_directory = _daemonize(options.pidfile)
    else:
        pid_directory = _write_pidfile(options.pidfile)

    # Everything below happens in a fixed order, and the order is the
    # design.  Work out who to become, fork the privileged helper while
    # there is still exactly one thread, bind the socket while the port
    # may still need privilege, hand the files over, drop, and only then
    # start any thread.
    try:
        # Ask whether this process can drop at all before a single file
        # changes hands.  Finding out afterwards would leave the files
        # belonging to an account the daemon never became.
        privsep.check_can_drop()
        account, last_resort = privsep.resolve_user(settings.daemon_user)
    except privsep.PrivilegeError as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR
    application.daemon_user = privsep.describe_user(account, last_resort)
    if last_resort:
        _LOG.warning('%s', application.daemon_user['warning'])

    try:
        helper = privsep.start_helper()
    except privsep.PrivilegeError as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR

    try:
        server = httpd.create_server(application)
    except OSError as err:
        print(f'kasapanel: cannot listen on {settings.listen_host}:'
              f'{settings.listen_port}: {err}', file=sys.stderr)
        if helper is not None:
            helper.close()
        return EXIT_ERROR
    application.server = server

    if os.getuid() == 0:
        privsep.own_tree(
            [settings.state_dir, application.config_store.path,
             settings.log_file, options.pidfile or '', pid_directory],
            account.pw_uid, account.pw_gid)
        _hand_over_directories(application, settings, account,
                               pid_directory)
        _warn_about_unreadable_tls(settings, account)
        _warn_about_the_pidfile(options.pidfile, account)
        try:
            privsep.drop_privileges(account)
        except privsep.PrivilegeError as err:
            print(f'kasapanel: {err}', file=sys.stderr)
            if helper is not None:
                helper.close()
            server.server_close()
            return EXIT_ERROR

    if helper is not None:
        application.helper = helper
        application.authenticator.use_verifier(_helper_verifier(helper))

    # Only now, as the account the daemon will actually run as, is it
    # worth asking what it can write.
    application.pidfile = options.pidfile or ''
    application.check_files()

    application.start()

    watcher = certwatch.CertificateWatcher(
        settings, server.tls_context, application.activity)
    watcher.adopt(settings.resolved_certificate())
    watcher.start()
    application.certwatch = watcher

    stopping = threading.Event()
    failure = threading.Event()

    def _helper_died(reason: str) -> None:
        """Brings the daemon down when the helper does.

        Nobody can sign in without the helper, and it cannot be started
        again from here: this process gave up the privilege to do that
        when it dropped.  Exiting non-zero lets the service manager
        start a whole daemon again.

        Args:
            reason: How the helper ended.
        """
        message = (f'the authentication helper has stopped ({reason}); '
                   'shutting down so the daemon can be restarted whole')
        _LOG.critical('%s', message)
        application.activity.add(message, level='error', source='daemon')
        failure.set()
        stopping.set()

    if helper is not None:
        helper.watch(_helper_died)

    def _signalled(number: int, _: object) -> None:
        """Asks the daemon to stop.

        Args:
            number: The signal number received.
            _: Unused stack frame.
        """
        _LOG.info('signal %d received, shutting down', number)
        stopping.set()

    def _hangup(number: int, _: object) -> None:
        """Looks at the certificate file straight away.

        Args:
            number: The signal number received.
            _: Unused stack frame.
        """
        _LOG.info('signal %d received, checking the certificate file', number)
        watcher.check_now()

    signal.signal(signal.SIGINT, _signalled)
    signal.signal(signal.SIGTERM, _signalled)
    signal.signal(signal.SIGHUP, _hangup)

    serving = threading.Thread(target=server.serve_forever,
                               kwargs={'poll_interval': 0.5},
                               name='kasa-http', daemon=True)
    serving.start()
    print(f'kasapanel {__version__} listening on '
          f'https://{settings.listen_host}:{settings.listen_port}/')
    try:
        while not stopping.wait(timeout=1.0):
            pass
    finally:
        watcher.stop()
        server.shutdown()
        server.server_close()
        application.stop()
        if application.helper is not None:
            application.helper.close()
        _remove_pidfile(options.pidfile)
    if failure.is_set():
        print('kasapanel: the authentication helper stopped; exiting so '
              'the daemon can be restarted', file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def command_check(options: argparse.Namespace) -> int:
    """Checks the configuration, the inventory and every schedule.

    Args:
        options: Parsed command line options.

    Returns:
        A process exit status; non-zero when a problem was found.
    """
    try:
        application = _prepare(options)
    except jsonstore.StoreError as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR
    problems = 0
    schedule_problems = 0
    records = application.devices.records()
    print(f'configuration: {application.config_store.path}')
    print(f'state directory: {application.settings.state_dir}')
    print(f'devices: {len(records)}')
    for record in records:
        document = application.devices.device_document(record.device_id)
        entries, found = schedule_lib.parse_script(
            document.get('schedule', ''))
        status = 'off' if not document.get('schedule_enabled', True) else 'on'
        print(f'  {record.display_name} ({record.host}) '
              f'[{record.device_id}] schedule {status}, '
              f'{len(entries)} rule(s)')
        for problem in found:
            schedule_problems += 1
            print(f'    line {problem.line_number}: {problem.message}',
                  file=sys.stderr)
    certificate = application.settings.resolved_certificate()
    if os.path.isfile(certificate):
        print(f'certificate: {certificate}')
        print(f'fingerprint: {certs.fingerprint(certificate)}')
    else:
        print(f'certificate: not created yet ({certificate})')
    daemon_account = None
    try:
        account, last_resort = privsep.resolve_user(
            application.settings.daemon_user)
        daemon_account = account if os.getuid() == 0 else None
        print(f'daemon user: {account.pw_name} '
              f'(uid {account.pw_uid}, gid {account.pw_gid})')
        if last_resort:
            problems += 1
            print('  this is the last-resort account; set daemon_user to '
                  'a dedicated one', file=sys.stderr)
        for path in (application.settings.resolved_certificate(),
                     application.settings.resolved_key()):
            if path and not privsep.readable_by(path, account):
                print(f'  {path} is not readable by {account.pw_name}; a '
                      'renewed certificate could not be loaded',
                      file=sys.stderr)
    except privsep.PrivilegeError as err:
        problems += 1
        print(f'daemon user: {err}', file=sys.stderr)
    application.check_files(daemon_account)
    if daemon_account is not None:
        print(f'file checks asked on behalf of {daemon_account.pw_name}, '
              'the account the daemon would run as')
    else:
        whoami = pwd.getpwuid(os.getuid()).pw_name
        print(f'file checks ran as {whoami}')
    for finding in filecheck.problems(application.file_checks):
        problems += 1
        purpose = finding['purpose']
        where = finding['path']
        origin = finding['source']
        trouble = finding['problem']
        result = finding['consequence']
        print(f'{purpose}: {where} ({origin})\n  {trouble}\n  {result}',
              file=sys.stderr)
    print(f'capabilities: {privsep.capability_report()}')
    if os.getuid() == 0:
        print(f'privileges: {privsep.describe_privileges()}')
        print(f'capabilities: {privsep.capability_report()}')
    absent = privsep.missing_capabilities()
    if absent and os.getuid() == 0:
        problems += 1
        print(f'capabilities: {privsep.capability_advice(absent)}',
              file=sys.stderr)
    report = auth.pam_diagnosis()
    interpreter = report['interpreter']
    module = report['module']
    libpam = report['libpam'] or 'not found by ldconfig'
    print(f'python interpreter: {interpreter}')
    if report['available']:
        print(f'pam: {module} '
              f'(service {application.settings.pam_service})')
        print(f'libpam: {libpam}')
    else:
        problems += 1
        reason = report['reason']
        advice = report['advice']
        print(f'pam: unusable, nobody can sign in\n  {reason}\n'
              f'  to fix it, run: {advice}', file=sys.stderr)
    if schedule_problems:
        print(f'{schedule_problems} schedule problem(s) found',
              file=sys.stderr)
    if problems or schedule_problems:
        return EXIT_ERROR
    return EXIT_OK


def command_paths(options: argparse.Namespace) -> int:
    """Prints the resolved file paths as JSON.

    Args:
        options: Parsed command line options.

    Returns:
        A process exit status.
    """
    application = _prepare(options)
    settings = application.settings
    print(json.dumps({
        'config': application.config_store.path,
        'state_dir': settings.state_dir,
        'devices': settings.devices_file,
        'device_dir': settings.device_dir,
        'certificate': settings.resolved_certificate(),
        'key': settings.resolved_key(),
    }, indent=2, sort_keys=True))
    return EXIT_OK


def command_gen_cert(options: argparse.Namespace) -> int:
    """Creates or replaces the self-signed TLS pair.

    Args:
        options: Parsed command line options.

    Returns:
        A process exit status.
    """
    application = _prepare(options)
    certificate = application.settings.resolved_certificate()
    key = application.settings.resolved_key()
    if options.force:
        for path in (certificate, key):
            if os.path.exists(path):
                os.unlink(path)
    try:
        certs.ensure_certificate(certificate, key, days=options.days)
    except certs.CertificateError as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR
    print(f'certificate: {certificate}')
    print(f'key: {key}')
    print(f'fingerprint: {certs.fingerprint(certificate)}')
    return EXIT_OK


COMMANDS = {
    'run': command_run,
    'check': command_check,
    'paths': command_paths,
    'gen-cert': command_gen_cert,
}


def main(argv: Optional[List[str]] = None) -> int:
    """Parses the command line and runs the chosen command.

    Args:
        argv: Argument list; defaults to the process arguments.

    Returns:
        A process exit status.
    """
    parser = build_parser()
    options = parser.parse_args(argv)
    handler = COMMANDS.get(options.command or 'run')
    if handler is None:  # pragma: no cover - argparse rejects these.
        parser.print_help()
        return EXIT_ERROR
    if options.command is None:
        options = parser.parse_args((argv or []) + ['run'])
    try:
        return handler(options)
    except (jsonstore.StoreError, devicestore.DeviceError) as err:
        print(f'kasapanel: {err}', file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - interactive use.
        return EXIT_OK


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
