# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Daemon configuration.

The daemon keeps its own settings in one flat JSON object so the file
stays readable and hand editable.  Device inventory and per-device
settings live in their own files; see :mod:`kasapanel.devicestore`.

Default locations depend on who runs the daemon.  Running as root uses
``/etc/kasapanel/config.json`` and ``/var/lib/kasapanel``; any other
user gets the equivalent paths under their home directory.
"""

import dataclasses
import os
from typing import Any, Dict, List, Set

from kasapanel import jsonstore

CONFIG_VERSION = 1

SYSTEM_CONFIG_DIR = '/etc/kasapanel'
SYSTEM_STATE_DIR = '/var/lib/kasapanel'

SENSITIVE_FIELDS = ('device_password',)


def default_config_path() -> str:
    """Returns the configuration file path for the current user.

    Returns:
        An absolute path; the file itself need not exist yet.
    """
    if os.geteuid() == 0:
        return os.path.join(SYSTEM_CONFIG_DIR, 'config.json')
    base = os.environ.get(
        'XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
    return os.path.join(base, 'kasapanel', 'config.json')


def default_state_dir() -> str:
    """Returns the state directory for the current user.

    Returns:
        An absolute path; the directory need not exist yet.
    """
    if os.geteuid() == 0:
        return SYSTEM_STATE_DIR
    base = os.environ.get(
        'XDG_STATE_HOME', os.path.expanduser('~/.local/state'))
    return os.path.join(base, 'kasapanel')


@dataclasses.dataclass
class PanelConfig:
    """Everything the daemon needs to start.

    Attributes:
        version: Schema version of the configuration file.
        state_dir: Directory holding devices.json and device files.
        listen_host: Address the HTTPS server binds to.
        listen_port: TCP port the HTTPS server binds to.
        tls_certificate: PEM certificate served to browsers.
        tls_key: PEM private key matching the certificate.
        daemon_user: Unprivileged account the daemon runs as once the
            privileged authentication helper has been forked.  Empty
            means www-data, then httpd, then nobody.
        pam_service: PAM service name used to check passwords.
        allowed_users: Users allowed to sign in; empty means any user
            that PAM accepts.
        allowed_groups: Groups whose members are allowed to sign in.
        session_minutes: Idle lifetime of a signed in session.
        max_login_failures: Failures before an account is locked out.
        lockout_seconds: How long a lockout lasts.
        poll_seconds: Interval between background device polls.
        discovery_seconds: How long a network scan listens for replies.
        discovery_target: Broadcast address used for network scans.
        command_timeout_seconds: Deadline for a single device command.
        device_username: Cloud account name for newer KLAP devices.
        device_password: Cloud account password for those devices.
        scheduler_enabled: Whether schedules run at all.
        history_limit: Number of activity records kept in memory.
        log_level: Root log level name, for example ``INFO``.
        log_file: Optional log file; empty logs to standard error.
    """

    version: int = CONFIG_VERSION
    state_dir: str = dataclasses.field(default_factory=default_state_dir)
    listen_host: str = '0.0.0.0'
    listen_port: int = 8443
    tls_certificate: str = ''
    tls_key: str = ''
    daemon_user: str = ''
    pam_service: str = 'login'
    allowed_users: List[str] = dataclasses.field(default_factory=list)
    allowed_groups: List[str] = dataclasses.field(default_factory=list)
    session_minutes: int = 720
    max_login_failures: int = 5
    lockout_seconds: int = 300
    poll_seconds: int = 30
    discovery_seconds: int = 8
    discovery_target: str = '255.255.255.255'
    command_timeout_seconds: int = 20
    device_username: str = ''
    device_password: str = ''
    scheduler_enabled: bool = True
    history_limit: int = 500
    metrics_enabled: bool = True
    log_level: str = 'INFO'
    log_file: str = ''

    @property
    def devices_file(self) -> str:
        """Returns the path of the device inventory file."""
        return os.path.join(self.state_dir, 'devices.json')

    @property
    def device_dir(self) -> str:
        """Returns the directory holding per-device JSON files."""
        return os.path.join(self.state_dir, 'devices')

    @property
    def tls_dir(self) -> str:
        """Returns the directory holding the TLS material."""
        return os.path.join(self.state_dir, 'tls')

    def resolved_certificate(self) -> str:
        """Returns the certificate path, filling in the default."""
        return self.tls_certificate or os.path.join(
            self.tls_dir, 'panel.crt')

    def resolved_key(self) -> str:
        """Returns the private key path, filling in the default."""
        return self.tls_key or os.path.join(self.tls_dir, 'panel.key')

    def to_dict(self) -> Dict[str, Any]:
        """Returns the configuration as a JSON friendly dictionary."""
        return dataclasses.asdict(self)

    def redacted(self) -> Dict[str, Any]:
        """Returns the configuration with secrets replaced by a flag.

        Returns:
            A dictionary safe to send to the browser.  Secret fields are
            replaced by a boolean ``<field>_set`` key.
        """
        document = self.to_dict()
        for field in SENSITIVE_FIELDS:
            document[f'{field}_set'] = bool(document.get(field))
            document.pop(field, None)
        return document


def _field_kinds() -> Dict[str, str]:
    """Maps each configuration field to a simple value kind.

    The kind is taken from the default value rather than the annotation
    so it stays correct however annotations are evaluated.

    Returns:
        Field names mapped to ``bool``, ``int``, ``str`` or ``list``.
    """
    kinds = {}
    template = PanelConfig()
    for field in dataclasses.fields(PanelConfig):
        default = getattr(template, field.name)
        if isinstance(default, bool):
            kinds[field.name] = 'bool'
        elif isinstance(default, int):
            kinds[field.name] = 'int'
        elif isinstance(default, list):
            kinds[field.name] = 'list'
        else:
            kinds[field.name] = 'str'
    return kinds


_FIELD_TYPES = _field_kinds()

TRUE_WORDS = ('1', 'true', 'yes', 'on')


def _coerce(name: str, value: Any, current: Any) -> Any:
    """Converts one incoming settings value to the declared kind.

    Args:
        name: Field name.
        value: Incoming value, typically decoded from JSON.
        current: The value currently in effect, returned on failure.

    Returns:
        The converted value, or ``current`` when conversion fails.
    """
    kind = _FIELD_TYPES.get(name)
    try:
        if kind == 'bool':
            if isinstance(value, str):
                return value.strip().lower() in TRUE_WORDS
            return bool(value)
        if kind == 'int':
            return int(value)
        if kind == 'str':
            return str(value)
        if kind == 'list':
            if isinstance(value, str):
                value = value.replace(',', ' ').split()
            return [str(item).strip() for item in value if str(item).strip()]
    except (TypeError, ValueError):
        return current
    return current


def from_dict(document: Dict[str, Any]) -> PanelConfig:
    """Builds a configuration from a decoded JSON document.

    Unknown keys are ignored and missing keys keep their default, so an
    older or hand trimmed file still loads.

    Args:
        document: Decoded configuration document.

    Returns:
        The configuration.
    """
    config = PanelConfig()
    for name in _FIELD_TYPES:
        if name in document:
            setattr(config, name,
                    _coerce(name, document[name], getattr(config, name)))
    config.version = CONFIG_VERSION
    return config


class ConfigStore:
    """Loads and saves :class:`PanelConfig` documents."""

    def __init__(self, path: str = ''):
        """Initialises the store.

        Args:
            path: Configuration file path; empty selects the default.
        """
        self._document = jsonstore.JsonDocument(
            path or default_config_path(), PanelConfig().to_dict())
        self._config = PanelConfig()
        self._explicit: Set[str] = set()

    @property
    def path(self) -> str:
        """Returns the path of the configuration file."""
        return self._document.path

    def is_explicit(self, name: str) -> bool:
        """Says whether a setting was written in the file.

        Args:
            name: Setting name.

        Returns:
            True when the configuration file names it, False when the
            value in force is the built-in default.
        """
        return name in self._explicit

    @property
    def config(self) -> PanelConfig:
        """Returns the configuration currently in effect."""
        return self._config

    def load(self) -> PanelConfig:
        """Reads the configuration file, creating it when missing.

        Returns:
            The configuration that is now in effect.
        """
        existed = os.path.exists(self._document.path)
        stored = self._document.load()
        known = set(PanelConfig().to_dict())
        # Remember which values the operator actually wrote down.  A
        # path that is merely a default reads differently in a warning
        # from one somebody chose.
        self._explicit = {name for name in stored if name in known}
        self._config = from_dict(stored)
        if not existed:
            self.save(self._config)
        jsonstore.ensure_directory(self._config.state_dir)
        jsonstore.ensure_directory(self._config.device_dir)
        jsonstore.ensure_directory(self._config.tls_dir)
        return self._config

    def save(self, config: PanelConfig) -> PanelConfig:
        """Writes a configuration to disk and makes it current.

        Args:
            config: The configuration to store.

        Returns:
            The stored configuration.
        """
        self._config = config
        self._document.replace(config.to_dict())
        return self._config

    def update(self, changes: Dict[str, Any]) -> PanelConfig:
        """Applies a partial update and saves the result.

        Args:
            changes: Field names mapped to new values.  Secret fields
                given as an empty string are left unchanged.

        Returns:
            The updated configuration.
        """
        document = self._config.to_dict()
        for name, value in changes.items():
            if name not in _FIELD_TYPES or name == 'version':
                continue
            if name in SENSITIVE_FIELDS and value == '':
                continue
            document[name] = value
            # Saved from the panel is as explicit as written by hand.
            self._explicit.add(name)
        return self.save(from_dict(document))
