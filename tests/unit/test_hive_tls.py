# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the TLS-enabled Hive/Kyuubi SQLAlchemy dialect.

The dialect lives in templates/hive_tls.py and is loaded inside the workload
container. These tests stub PyHive and Thrift so the module can be imported
and exercised without those packages installed.
"""

import importlib.util
import pathlib
import ssl
import sys
import types
import unittest

from sqlalchemy.engine.url import make_url

# ---------------------------------------------------------------------------
# Bootstrap: import the module with stubbed `pyhive`, `thrift` and
# `thrift_sasl` packages
# ---------------------------------------------------------------------------


class _FakeTSSLSocket:
    """Records the arguments the dialect builds the TLS socket with.

    Attrs:
        host: the host passed positionally.
        port: the port passed positionally.
        kwargs: the keyword arguments passed.
    """

    def __init__(self, host, port, **kwargs):
        """Store the constructor arguments.

        Args:
            host: the server host.
            port: the server port.
            kwargs: the remaining socket options.
        """
        self.host = host
        self.port = port
        self.kwargs = kwargs


class _FakeTSaslClientTransport:
    """Stand-in for the SASL transport wrapping the TLS socket.

    Attrs:
        sasl_factory: callable returning the SASL client.
        mechanism: the negotiated SASL mechanism.
        transport: the wrapped transport.
    """

    def __init__(self, sasl_factory, mechanism, transport):
        """Store the constructor arguments.

        Args:
            sasl_factory: callable returning the SASL client.
            mechanism: the SASL mechanism name.
            transport: the underlying transport.
        """
        self.sasl_factory = sasl_factory
        self.mechanism = mechanism
        self.transport = transport


def _fake_get_installed_sasl(**kwargs):
    """Return the SASL client arguments instead of a client.

    Args:
        kwargs: the arguments PyHive would build a SASL client from.

    Returns:
        The arguments, unchanged.
    """
    return kwargs


_pyhive_hive = types.ModuleType("pyhive.hive")
setattr(_pyhive_hive, "get_installed_sasl", _fake_get_installed_sasl)
_pyhive = types.ModuleType("pyhive")
setattr(_pyhive, "hive", _pyhive_hive)
_pyhive_sqlalchemy = types.ModuleType("pyhive.sqlalchemy_hive")
setattr(_pyhive_sqlalchemy, "HiveDialect", type("HiveDialect", (), {}))

_thrift_ssl_socket = types.ModuleType("thrift.transport.TSSLSocket")
setattr(_thrift_ssl_socket, "TSSLSocket", _FakeTSSLSocket)
_thrift_sasl = types.ModuleType("thrift_sasl")
setattr(_thrift_sasl, "TSaslClientTransport", _FakeTSaslClientTransport)

sys.modules.setdefault("pyhive", _pyhive)
sys.modules.setdefault("pyhive.hive", _pyhive_hive)
sys.modules.setdefault("pyhive.sqlalchemy_hive", _pyhive_sqlalchemy)
sys.modules.setdefault("thrift", types.ModuleType("thrift"))
sys.modules.setdefault(
    "thrift.transport", types.ModuleType("thrift.transport")
)
sys.modules.setdefault("thrift.transport.TSSLSocket", _thrift_ssl_socket)
sys.modules.setdefault("thrift_sasl", _thrift_sasl)

_MODULE_PATH = (
    pathlib.Path(__file__).parent.parent.parent / "templates" / "hive_tls.py"
)
_spec = importlib.util.spec_from_file_location("hive_tls", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
hive_tls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hive_tls)


class TestSslContext(unittest.TestCase):
    """The SSL context reflects the verification query parameters."""

    def test_verifies_and_checks_hostname_by_default(self):
        """With no overrides, the context verifies and checks hostnames."""
        context = hive_tls.build_ssl_context(
            ca_bundle=None, check_hostname=True, verify=True
        )
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_hostname_check_can_be_disabled_while_still_verifying(self):
        """Hostname checking can be turned off without disabling verify."""
        context = hive_tls.build_ssl_context(
            ca_bundle=None, check_hostname=False, verify=True
        )
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_verification_can_be_disabled(self):
        """Disabling verify also disables the hostname check."""
        context = hive_tls.build_ssl_context(
            ca_bundle=None, check_hostname=True, verify=False
        )
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_NONE)


class TestConnectArgs(unittest.TestCase):
    """A hive+tls URL maps onto PyHive's thrift_transport argument."""

    def _connect_args(self, uri):
        """Build the connection kwargs for a URI.

        Args:
            uri: the SQLAlchemy URI to translate.

        Returns:
            The keyword arguments the dialect would call PyHive with.
        """
        dialect = hive_tls.HiveTLSDialect()
        _, kwargs = dialect.create_connect_args(make_url(uri))
        return kwargs

    def test_transport_wraps_a_tls_socket(self):
        """The thrift transport wraps a TLS socket to the right host/port."""
        kwargs = self._connect_args(
            "hive+tls://admin:secret@kyuubi-0:10009/telemetry"
        )
        transport = kwargs["thrift_transport"]
        self.assertEqual(transport.mechanism, "PLAIN")
        self.assertIsInstance(transport.transport, _FakeTSSLSocket)
        self.assertEqual(transport.transport.host, "kyuubi-0")
        self.assertEqual(transport.transport.port, 10009)

    def test_host_port_and_credentials_are_not_passed_to_pyhive(self):
        """Only thrift_transport, username and database reach PyHive."""
        kwargs = self._connect_args(
            "hive+tls://admin:secret@kyuubi-0:10009/telemetry"
        )
        self.assertEqual(
            set(kwargs), {"thrift_transport", "username", "database"}
        )
        self.assertEqual(kwargs["username"], "admin")
        self.assertEqual(kwargs["database"], "telemetry")

    def test_defaults_to_the_kyuubi_thrift_binary_port(self):
        """A URL with no port defaults to Kyuubi's thrift binary port."""
        kwargs = self._connect_args("hive+tls://admin:secret@kyuubi-0/default")
        self.assertEqual(kwargs["thrift_transport"].transport.port, 10009)

    def test_thrift_hostname_check_is_bypassed(self):
        """Thrift's own hostname check is bypassed in favour of SSLContext."""
        kwargs = self._connect_args(
            "hive+tls://admin:secret@kyuubi-0:10009/telemetry"
        )
        socket_kwargs = kwargs["thrift_transport"].transport.kwargs
        self.assertEqual(socket_kwargs["server_hostname"], "kyuubi-0")
        self.assertIs(
            socket_kwargs["validate_callback"],
            hive_tls._skip_thrift_hostname_check,
        )

    def test_query_parameters_configure_verification(self):
        """The check_hostname query parameter configures the SSL context."""
        kwargs = self._connect_args(
            "hive+tls://admin:secret@kyuubi.svc:10009/telemetry"
            "?check_hostname=false"
        )
        context = kwargs["thrift_transport"].transport.kwargs["ssl_context"]
        self.assertFalse(context.check_hostname)

    def test_sasl_password_placeholder_when_absent(self):
        """A missing password is replaced with the PLAIN mechanism placeholder."""
        kwargs = self._connect_args("hive+tls://admin@kyuubi-0:10009/default")
        sasl_args = kwargs["thrift_transport"].sasl_factory()
        self.assertEqual(sasl_args["password"], "x")
