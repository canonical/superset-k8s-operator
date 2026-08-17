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
        is_related: Whether an OAuth provider relation exists.
        provider_info: Live provider information when registration is ready.
    """

    def __init__(self, charm):
        """Construct the OAuth relation handler.

        Args:
            charm: Superset charm instance.
        """
        super().__init__(charm, OAUTH_RELATION_NAME)
        self.charm = charm
        self._provider_removed = False
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
            self._on_oauth_info_changed,
        )
        self.framework.observe(
            self.requirer.on.oauth_info_removed,
            self._on_oauth_info_removed,
        )
        self.framework.observe(
            self.requirer.on.invalid_client_config,
            self._on_invalid_client_config,
        )

    @property
    def _client_config(self) -> ClientConfig:
        """Build the client registration data from charm configuration."""
        external_hostname = (
            self.charm.model.config.get("external-hostname")
            or self.charm.app.name
        )
        return ClientConfig(
            redirect_uri=(f"https://{external_hostname}{OAUTH_CALLBACK_PATH}"),
            scope=OAUTH_SCOPE,
            grant_types=OAUTH_GRANT_TYPES,
        )

    @property
    def is_related(self) -> bool:
        """Return whether an OAuth provider is related."""
        return self.charm.model.get_relation(OAUTH_RELATION_NAME) is not None

    @property
    def provider_info(self) -> Optional[OauthProviderConfig]:
        """Return live provider details once registration is complete."""
        if (
            self._provider_removed
            or not self.is_related
            or not self.requirer.is_client_created()
        ):
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
        if not self.is_related:
            return
        self.requirer.update_client_config(self._client_config)

    def _on_oauth_relation_created(self, event) -> None:
        """Publish client data when a provider relation is created.

        Args:
            event: OAuth relation-created event.
        """
        try:
            self.publish_client_config()
        except ClientConfigError as exc:
            logger.error("Invalid OAuth client configuration: %s", exc)
            self.charm.unit.status = BlockedStatus(
                "invalid OAuth client configuration"
            )

    def _on_oauth_info_changed(self, event) -> None:
        """Reconfigure Superset when provider information changes."""
        self._provider_removed = False
        self.charm._update(event)

    def _on_oauth_info_removed(self, event) -> None:
        """Remove OAuth configuration when provider information disappears."""
        self._provider_removed = True
        self.charm._update(event)

    def _on_invalid_client_config(self, event) -> None:
        """Log client configuration rejected by the relation library."""
        logger.error("Invalid OAuth client configuration: %s", event.error)
        self.charm.unit.status = BlockedStatus(
            "invalid OAuth client configuration"
        )


__all__ = ["ClientConfigError", "OAuthRelation"]
