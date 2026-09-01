"""SQLAlchemy dialect for TLS-enabled Hive/Kyuubi Thrift binary endpoints.

PyHive only wires TLS into its HTTP transport, registered as ``hive+https``,
which POSTs to ``/cliservice`` and therefore needs a server running the
``THRIFT_HTTP`` frontend. Charmed Kyuubi serves ``THRIFT_BINARY`` with
``kyuubi.frontend.thrift.binary.ssl.enabled=true``: SASL framing straight over
a TLS socket. PyHive's binary path (``hive://``) builds a plain ``TSocket``,
so neither of its dialects can reach a TLS-enabled Kyuubi.

This dialect keeps PyHive's binary/SASL stack and only swaps the plain socket
for a ``TSSLSocket``, handing the result to PyHive through its documented
``thrift_transport`` argument.

Usage::

    hive+tls://user:password@host:10009/database

Query parameters:
    ssl_cert: PEM bundle to verify the server against. Defaults to the system
        trust store, into which the charm installs the CA received over the
        ``certificates`` relation.
    check_hostname: Set to ``false`` when connecting through a name that the
        server certificate does not carry, such as a Kubernetes ``Service``
        DNS name when Kyuubi only requests SANs for its pod and node.
    ssl_verify: Set to ``false`` to skip certificate verification entirely.
"""

import ssl
from typing import Any, Optional

from pyhive import hive
from pyhive.sqlalchemy_hive import HiveDialect
from thrift.transport.TSSLSocket import TSSLSocket
from thrift_sasl import TSaslClientTransport

KYUUBI_THRIFT_BINARY_PORT = 10009

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _as_bool(value: Any, default: bool) -> bool:
    """Interpret a URI query parameter as a boolean.

    Args:
        value: Raw query parameter value, or None if absent.
        default: Value to return when the parameter is absent.

    Returns:
        The parsed boolean.
    """
    if value is None:
        return default
    return str(value).strip().lower() in _TRUTHY


def build_ssl_context(
    ca_bundle: Optional[str], check_hostname: bool, verify: bool
) -> ssl.SSLContext:
    """Build the client SSL context used for the Thrift socket.

    Args:
        ca_bundle: PEM bundle to verify the server against, or None to use the
            system trust store.
        check_hostname: Whether the server name must match the certificate.
        verify: Whether the server certificate is verified at all.

    Returns:
        The configured SSL context.
    """
    context = ssl.create_default_context(cafile=ca_bundle)
    if not verify:
        # check_hostname must be cleared before CERT_NONE, the setter rejects
        # the opposite order.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    context.check_hostname = check_hostname
    return context


def _skip_thrift_hostname_check(cert: Any, hostname: Any) -> None:
    """Accept the peer certificate without re-checking its hostname.

    Thrift runs its own post-handshake hostname check, but on Python 3.12 and
    later it silently degrades to a common-name-only comparison because
    ``ssl.match_hostname`` was removed. That rejects certificates carrying the
    name in a SAN only, which is what Kyuubi requests. The SSL context above
    already performs the real, SAN-aware check.

    Args:
        cert: Peer certificate, unused.
        hostname: Server name, unused.
    """


class HiveTLSDialect(HiveDialect):
    """Hive dialect speaking SASL over a TLS socket."""

    name = "hive"
    driver = "tls"
    supports_statement_cache = False

    def create_connect_args(self, url):
        """Translate a ``hive+tls`` URL into PyHive connection arguments.

        Args:
            url: The SQLAlchemy URL to connect with.

        Returns:
            A tuple of positional and keyword arguments for ``hive.connect``.
        """
        query = dict(url.query)
        host = url.host
        port = url.port or KYUUBI_THRIFT_BINARY_PORT
        username = url.username
        # SASL PLAIN rejects an empty password; PyHive substitutes the same
        # placeholder when authentication is not password based.
        password = url.password or "x"

        socket = TSSLSocket(
            host,
            port,
            ssl_context=build_ssl_context(
                ca_bundle=query.get("ssl_cert"),
                check_hostname=_as_bool(query.get("check_hostname"), True),
                verify=_as_bool(query.get("ssl_verify"), True),
            ),
            server_hostname=host,
            validate_callback=_skip_thrift_hostname_check,
        )
        transport = TSaslClientTransport(
            lambda: hive.get_installed_sasl(
                host=host,
                sasl_auth="PLAIN",
                username=username,
                password=password,
            ),
            "PLAIN",
            socket,
        )

        return [], {
            "thrift_transport": transport,
            "username": username,
            "database": url.database or "default",
        }
