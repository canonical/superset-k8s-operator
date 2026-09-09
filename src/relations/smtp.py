#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset SMTP relation."""

import logging
from typing import Optional

from charms.smtp_integrator.v0.smtp import (
    AuthType,
    SmtpError,
    SmtpRelationData,
    SmtpRequires,
    TransportSecurity,
)
from ops.framework import Object
from pydantic import ValidationError

from literals import SMTP_RELATION_NAME

logger = logging.getLogger(__name__)


class SmtpRelation(Object):
    """Map the relay published by an SMTP provider onto Superset's settings.

    Attrs:
        charm: Superset charm instance.
        requirer: SMTP relation library instance.
    """

    def __init__(self, charm):
        """Construct the SMTP relation handler.

        Args:
            charm: Superset charm instance.
        """
        super().__init__(charm, SMTP_RELATION_NAME)
        self.charm = charm
        self.requirer = SmtpRequires(charm, relation_name=SMTP_RELATION_NAME)
        self.framework.observe(
            self.requirer.on.smtp_data_available,
            self._on_reconcile,
        )
        self.framework.observe(
            charm.on[SMTP_RELATION_NAME].relation_broken,
            self._on_reconcile,
        )

    def is_related(self) -> bool:
        """Return whether an SMTP provider is related.

        Returns:
            True when a provider relation exists.
        """
        return self.charm.model.get_relation(SMTP_RELATION_NAME) is not None

    def relation_data(self) -> Optional[SmtpRelationData]:
        """Return the relay details the provider published.

        Returns:
            The relation data, or None when there is no relation or the
            provider has not published anything yet.

        Raises:
            ValueError: when the provider published data the library refuses,
                the password secret cannot be read, or the relay carries no
                sender address, which Superset requires and the interface
                leaves optional.
        """
        if not self.is_related():
            return None

        try:
            data = self.requirer.get_relation_data()
        except (SmtpError, ValidationError) as exc:
            raise ValueError(
                f"smtp relation data is unusable: {exc}"
            ) from None

        if data is not None and not data.smtp_sender:
            raise ValueError(
                "the smtp relation is missing smtp_sender, which Superset "
                "sends the mail from"
            )

        return data

    def environment(self) -> dict:
        """Return the SMTP relay as workload environment values.

        `recipients` is deliberately left out: Superset chooses the recipients
        of each alert and report in the UI, so it has nothing to read a
        relation-wide list into.

        Returns:
            The SMTP environment values, empty when no provider is related or
            it has published nothing yet.
        """
        data = self.relation_data()
        if data is None:
            return {}

        env = {
            "SMTP_HOST": data.host,
            "SMTP_PORT": data.port,
            "SMTP_EMAIL": data.smtp_sender,
            "SMTP_STARTTLS": (
                data.transport_security == TransportSecurity.STARTTLS
            ),
            "SMTP_SSL": data.transport_security == TransportSecurity.TLS,
            "SMTP_SSL_SERVER_AUTH": not data.skip_ssl_verify,
        }

        if data.auth_type == AuthType.PLAIN:
            env["SMTP_USERNAME"] = data.user or ""
            env["SMTP_PASSWORD"] = data.password or ""

        return env

    def _on_reconcile(self, event) -> None:
        """Reconfigure Superset when the provider relation changes.

        Covers both the relay details changing and the relation going away,
        which drops the SMTP settings from the workload environment.

        Args:
            event: SMTP data available or relation-broken event.
        """
        self.charm.reconcile()
