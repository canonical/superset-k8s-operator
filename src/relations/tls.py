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

        Installs the CA certificate into the workload container.

        Args:
            event: The event emitted when a certificate becomes available.
        """
        container = self.charm.unit.get_container(self.charm.name)
        if not container.can_connect():
            logger.debug("Container not ready, deferring CA installation.")
            event.defer()
            return

        self._write_ca(container, event.ca.raw)

    @log_event_handler(logger)
    def _on_certificates_broken(
        self, event: ops.RelationBrokenEvent
    ) -> None:
        """Handle the certificates-relation-broken event.

        Removes the CA certificate from the workload container.

        Args:
            event: The event emitted when the relation is broken.
        """
        container = self.charm.unit.get_container(self.charm.name)
        if not container.can_connect():
            logger.debug("Container not ready, deferring CA removal.")
            event.defer()
            return

        self._remove_ca(container)

    def _write_ca(self, container: ops.Container, ca: str) -> None:
        """Install the CA certificate into the container trust store.

        Args:
            container: The workload container.
            ca: The CA certificate in PEM format.
        """
        ca_pem = ca if ca.endswith("\n") else f"{ca}\n"
        container.push(CA_CERT_PATH, ca_pem, make_dirs=True, permissions=0o644)
        container.push(
            CA_CERT_LOCAL_PATH, ca_pem, make_dirs=True, permissions=0o644
        )
        self._update_ca_certificates(container)
        self._restart_workload(container)

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
        self._restart_workload(container)

    def _update_ca_certificates(
        self, container: ops.Container, fresh: bool = False
    ) -> None:
        """Run update-ca-certificates inside the container.

        Args:
            container: The workload container.
            fresh: Whether to regenerate the trust store from scratch.
        """
        command = ["update-ca-certificates"]
        if fresh:
            command.append("--fresh")
        try:
            container.exec(command).wait_output()
        except ops.pebble.ExecError as e:
            logger.error("Failed to update CA certificates: %s", e.stderr)

    def _restart_workload(self, container: ops.Container) -> None:
        """Restart the workload so new TLS material is picked up.

        Args:
            container: The workload container.
        """
        try:
            container.get_service(self.charm.name)
        except ops.ModelError:
            logger.debug("Workload service not present yet, skipping restart.")
            return
        container.restart(self.charm.name)
