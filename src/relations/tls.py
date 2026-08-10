# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""TLS certificates relation handler for the Superset charm.

Manages the ``certificates`` relation (``tls-certificates`` interface).

Superset acts as a TLS *client* towards data sources such as a TLS-enabled
Kyuubi/Hive Thrift endpoint. To validate those handshakes the workload needs
the provider's CA certificate. This handler requests a certificate from a TLS
provider (e.g. ``self-signed-certificates``), installs the returned CA into the
container's system trust store and writes it to a stable PEM path that can be
referenced from a Superset database connection's ``connect_args.ssl_cert``.
"""

import logging
from typing import Optional

import ops
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateAvailableEvent,
    CertificateRequestAttributes,
    TLSCertificatesRequiresV4,
)

from literals import (
    CA_CERT_LOCAL_PATH,
    CA_CERT_PATH,
    CERTIFICATES_RELATION_NAME,
)
from log import log_event_handler

logger = logging.getLogger(__name__)


class CertificateInstallError(Exception):
    """Raised when the workload CA trust store could not be updated."""


class Certificates(ops.Object):
    """Client for the superset:certificates relation.

    Observes the relation lifecycle and keeps the workload container's CA
    trust store in sync with the CA delivered by the TLS provider.
    """

    def __init__(self, charm: ops.CharmBase):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, CERTIFICATES_RELATION_NAME)
        self.charm = charm

        self.certificates = TLSCertificatesRequiresV4(
            charm=self.charm,
            relationship_name=CERTIFICATES_RELATION_NAME,
            certificate_requests=[self._certificate_request],
        )

        self.framework.observe(
            self.certificates.on.certificate_available,
            self._on_certificate_available,
        )
        self.framework.observe(
            self.charm.on[CERTIFICATES_RELATION_NAME].relation_broken,
            self._on_certificates_broken,
        )

    @property
    def _certificate_request(self) -> CertificateRequestAttributes:
        """Return the attributes of the certificate to request."""
        common_name = self.charm.app.name
        return CertificateRequestAttributes(common_name=common_name)

    @log_event_handler(logger)
    def _on_certificate_available(
        self, event: CertificateAvailableEvent
    ) -> None:
        """Handle the certificate-available event.

        Args:
            event: The event emitted when a certificate becomes available.
        """
        self.charm.reconcile_certificates()

    @log_event_handler(logger)
    def _on_certificates_broken(self, event: ops.RelationBrokenEvent) -> None:
        """Handle the certificates-relation-broken event.

        Args:
            event: The event emitted when the relation is broken.
        """
        self.charm.reconcile_certificates(relation_broken=True)

    def reconcile(
        self, container: ops.Container, relation_broken: bool = False
    ) -> bool:
        """Align the container trust store with the relation state.

        Args:
            container: The workload container.
            relation_broken: Whether the relation is being removed, in which
                case the assigned certificate is treated as gone even though
                the relation data is still readable.

        Returns:
            True if the trust store was modified, False if it was up to date.
        """
        desired = None if relation_broken else self._assigned_ca()
        if desired == self._installed_ca(container):
            return False

        if desired is None:
            self._remove_ca(container)
        else:
            self._write_ca(container, desired)
        return True

    def _assigned_ca(self) -> Optional[str]:
        """Return the CA assigned by the provider, if any."""
        provider_certificate, _ = self.certificates.get_assigned_certificate(
            certificate_request=self._certificate_request
        )
        if not provider_certificate:
            return None
        return _normalise_pem(provider_certificate.ca.raw)

    def _installed_ca(self, container: ops.Container) -> Optional[str]:
        """Return the CA currently installed in the container, if any.

        Args:
            container: The workload container.

        Returns:
            The installed CA in PEM format, or None if no CA is installed.
        """
        try:
            return _normalise_pem(container.pull(CA_CERT_PATH).read())
        except ops.pebble.PathError:
            return None

    def _write_ca(self, container: ops.Container, ca: str) -> None:
        """Install the CA certificate into the container trust store.

        Args:
            container: The workload container.
            ca: The CA certificate in PEM format.
        """
        for path in (CA_CERT_PATH, CA_CERT_LOCAL_PATH):
            container.push(path, ca, make_dirs=True, permissions=0o644)
        self._update_ca_certificates(container)

    def _remove_ca(self, container: ops.Container) -> None:
        """Remove the charm-managed CA certificate from the container.

        Args:
            container: The workload container.
        """
        for path in (CA_CERT_LOCAL_PATH, CA_CERT_PATH):
            try:
                container.remove_path(path, recursive=False)
            except ops.pebble.PathError:
                logger.debug("CA file %s already absent.", path)

        self._update_ca_certificates(container, fresh=True)

    def _update_ca_certificates(
        self, container: ops.Container, fresh: bool = False
    ) -> None:
        """Run update-ca-certificates inside the container.

        Args:
            container: The workload container.
            fresh: Whether to regenerate the trust store from scratch.

        Raises:
            CertificateInstallError: So callers do not report a healthy unit
                while the trust store is actually stale.
        """
        command = ["update-ca-certificates"]
        if fresh:
            command.append("--fresh")
        try:
            container.exec(command).wait_output()
        except ops.pebble.ExecError as e:
            raise CertificateInstallError(
                f"failed to update CA trust store: {e.stderr}"
            ) from e


def _normalise_pem(pem: str) -> str:
    """Return the PEM material with a single trailing newline.

    Args:
        pem: The PEM-encoded material.

    Returns:
        The normalised PEM material.
    """
    return f"{pem.strip()}\n"
