# SPDX-FileCopyrightText: 2026 Kasa Panel contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# SPDX-FileContributor: Drafted with AI assistance (Anthropic Claude);
# see docs/ai-bom.spdx.json for the AI usage declaration.
"""Self-signed TLS material for a first run.

A panel on a home network rarely has a certificate signed by a public
authority, so the daemon can make its own.  Browsers will warn about it
once and then let the operator continue; anyone who has a real
certificate can point the configuration at it instead.

The ``cryptography`` package is used when available, which it normally
is because python-kasa depends on it.  Otherwise the ``openssl`` command
is used.
"""

import datetime
import ipaddress
import logging
import os
import shutil
import socket
import subprocess
from typing import List, Tuple

from kasapanel import jsonstore

_LOG = logging.getLogger(__name__)

KEY_MODE = 0o600
CERT_MODE = 0o644
DEFAULT_DAYS = 3650


class CertificateError(Exception):
    """Raised when TLS material cannot be created."""


def local_addresses() -> List[str]:
    """Guesses the names and addresses this host answers to.

    Returns:
        Host names and IP addresses, most specific first.
    """
    names = ['localhost']
    addresses = ['127.0.0.1']
    hostname = socket.gethostname()
    if hostname:
        names.insert(0, hostname)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(('198.51.100.1', 53))
        addresses.insert(0, probe.getsockname()[0])
    except OSError:  # pragma: no cover - host without a default route.
        pass
    finally:
        probe.close()
    return names + addresses


def _generate_with_cryptography(certificate: str, key: str,
                                days: int) -> None:
    """Writes a self-signed pair using the cryptography package.

    Args:
        certificate: Path of the certificate to write.
        key: Path of the private key to write.
        days: How long the certificate stays valid.

    Raises:
        ImportError: If the cryptography package is missing.
    """
    # pylint: disable=import-outside-toplevel
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537,
                                           key_size=3072)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname()
                           or 'kasapanel'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Kasa Panel'),
    ])
    alternatives = []
    for entry in local_addresses():
        try:
            alternatives.append(x509.IPAddress(ipaddress.ip_address(entry)))
        except ValueError:
            alternatives.append(x509.DNSName(entry))
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(alternatives),
                       critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0),
                       critical=True))
    signed = builder.sign(private_key, hashes.SHA256())
    with open(key, 'wb') as handle:
        handle.write(private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()))
    with open(certificate, 'wb') as handle:
        handle.write(signed.public_bytes(serialization.Encoding.PEM))


def _generate_with_openssl(certificate: str, key: str, days: int) -> None:
    """Writes a self-signed pair by calling the openssl command.

    Args:
        certificate: Path of the certificate to write.
        key: Path of the private key to write.
        days: How long the certificate stays valid.

    Raises:
        CertificateError: If openssl is missing or fails.
    """
    openssl = shutil.which('openssl')
    if not openssl:
        raise CertificateError(
            'install the cryptography package or the openssl command, or '
            'point tls_certificate and tls_key at your own files')
    common_name = socket.gethostname() or 'kasapanel'
    names = []
    for index, entry in enumerate(local_addresses(), start=1):
        try:
            ipaddress.ip_address(entry)
            names.append(f'IP.{index}:{entry}')
        except ValueError:
            names.append(f'DNS.{index}:{entry}')
    command = [
        openssl, 'req', '-x509', '-newkey', 'rsa:3072', '-nodes',
        '-keyout', key, '-out', certificate, '-days', str(days),
        '-subj', f'/CN={common_name}',
        '-addext', 'subjectAltName=' + ','.join(names),
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0:
        raise CertificateError(
            'openssl failed: ' + result.stderr.decode('utf-8', 'replace')
            .strip())


def ensure_certificate(certificate: str, key: str,
                       days: int = DEFAULT_DAYS) -> Tuple[str, str]:
    """Creates a self-signed pair when one is not already on disk.

    Args:
        certificate: Path of the certificate.
        key: Path of the private key.
        days: How long a newly created certificate stays valid.

    Returns:
        The certificate and key paths.

    Raises:
        CertificateError: If the pair is missing and cannot be created.
    """
    if os.path.isfile(certificate) and os.path.isfile(key):
        return certificate, key
    jsonstore.ensure_directory(os.path.dirname(os.path.abspath(certificate)))
    jsonstore.ensure_directory(os.path.dirname(os.path.abspath(key)))
    _LOG.warning('creating a self-signed certificate at %s', certificate)
    try:
        _generate_with_cryptography(certificate, key, days)
    except ImportError:
        _generate_with_openssl(certificate, key, days)
    except OSError as err:
        raise CertificateError(f'cannot write TLS material: {err}') from err
    os.chmod(key, KEY_MODE)
    os.chmod(certificate, CERT_MODE)
    return certificate, key


def fingerprint(certificate: str) -> str:
    """Returns the SHA-256 fingerprint of a certificate.

    Args:
        certificate: Path of the PEM certificate.

    Returns:
        The fingerprint as colon separated hex pairs, or an empty string
        when it cannot be read.
    """
    # pylint: disable=import-outside-toplevel
    import hashlib
    import ssl
    try:
        with open(certificate, 'r', encoding='ascii') as handle:
            der = ssl.PEM_cert_to_DER_cert(handle.read())
    except (OSError, ValueError):
        return ''
    digest = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(digest[index:index + 2]
                    for index in range(0, len(digest), 2))
