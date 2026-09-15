#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset OAuth relation."""

import logging
from typing import Optional

from charms.hydra.v0.oauth import (
    ClientConfig,
    ClientConfigError,
    OauthProviderConfig,
    OAuthRequirer,
)
from ops import BlockedStatus, ModelError, SecretNotFoundError
from ops.framework import Object

from literals import (
    OAUTH_CALLBACK_PATH,
    OAUTH_GRANT_TYPES,
    OAUTH_RELATION_NAME,
    OAUTH_SCOPE,
)

logger = logging.getLogger(__name__)


class OAuthRelation(Object):
    """Manage client registration and provider data for the OAuth relation.

    Attrs:
        charm: Superset charm instance.
        requirer: OAuth relation library instance.
    """

    def __init__(self, charm):
        """Construct the OAuth relation handler.

        Args:
            charm: Superset charm instance.
        """
        super().__init__(charm, OAUTH_RELATION_NAME)
        self.charm = charm
        self.requirer = OAuthRequirer(
            charm,
            client_config=None,
            relation_name=OAUTH_RELATION_NAME,
        )
        self.framework.observe(
            charm.on[OAUTH_RELATION_NAME].relation_created,
            self._on_oauth_relation_created,
        )
        self.framework.observe(
            self.requirer.on.oauth_info_changed,
            self._on_reconcile,
        )
        self.framework.observe(
            charm.on[OAUTH_RELATION_NAME].relation_broken,
            self._on_reconcile,
        )
        self.framework.observe(
            self.requirer.on.invalid_client_config,
            self._on_invalid_client_config,
        )

    @property
    def _client_config(self) -> Optional[ClientConfig]:
        """Build the client registration data from the ingress URL.

        The external URL is published by the ingress provider, so it is only
        known once that relation is ready.
        """
        ingress_url = self.charm.https_ingress_url
        if ingress_url is None:
            return None
        return ClientConfig(
            redirect_uri=f"{ingress_url}{OAUTH_CALLBACK_PATH}",
            scope=OAUTH_SCOPE,
            grant_types=OAUTH_GRANT_TYPES,
        )

    def is_related(self) -> bool:
        """Return whether an OAuth provider is related.

        Returns:
            True when a provider relation exists.
        """
        return self.charm.model.get_relation(OAUTH_RELATION_NAME) is not None

    def provider_info(self) -> Optional[OauthProviderConfig]:
        """Return live provider details once registration is complete.

        Returns:
            The provider configuration, or None when the client registration
            is not complete or the relation is going away.
        """
        if not self.is_related() or not self.requirer.is_client_created():
            return None
        try:
            relation = self.charm.model.get_relation(OAUTH_RELATION_NAME)
            if relation and relation.app:
                client_secret_id = relation.data[relation.app].get(
                    "client_secret_id"
                )
                if client_secret_id:
                    self.charm.model.get_secret(
                        id=client_secret_id
                    ).get_content(refresh=True)
            return self.requirer.get_provider_info()
        except (SecretNotFoundError, ModelError) as exc:
            logger.info("OAuth client credentials are not ready: %s", exc)
            return None

    def publish_client_config(self) -> None:
        """Publish current client registration data on the relation."""
        if not self.is_related():
            return
        client_config = self._client_config
        if client_config is None:
            logger.info(
                "OAuth client config not published: "
                "waiting for an HTTPS ingress URL"
            )
            return
        self.requirer.update_client_config(client_config)

    def _on_oauth_relation_created(self, event) -> None:
        """Publish client data when a provider relation is created.

        Args:
            event: OAuth relation-created event.
        """
        try:
            self.publish_client_config()
        except ClientConfigError as exc:
            logger.error("Invalid OAuth client configuration: %s", exc)
            self.charm.report_failure(
                BlockedStatus("invalid OAuth client configuration")
            )

    def _on_reconcile(self, event) -> None:
        """Reconfigure Superset when the provider relation changes.

        Covers both provider information changing and the relation going
        away, which drops the OAuth settings from the workload environment.

        Args:
            event: OAuth information changed or relation-broken event.
        """
        self.charm.reconcile()

    def _on_invalid_client_config(self, event) -> None:
        """Log client configuration rejected by the relation library."""
        logger.error("Invalid OAuth client configuration: %s", event.error)
        self.charm.report_failure(
            BlockedStatus("invalid OAuth client configuration")
        )


__all__ = ["ClientConfigError", "OAuthRelation"]
