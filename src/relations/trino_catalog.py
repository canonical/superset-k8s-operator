# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Trino Catalog relation handler for the Superset charm.

Manages the trino-catalog relation, syncing Trino catalogs as
Superset database connections via the Superset REST API.
"""

import logging
from typing import Any

import ops
from charms.trino_k8s.v0.trino_catalog import (
    TrinoCatalog,
    TrinoCatalogRequirer,
)

from literals import TRINO_CATALOG_RELATION_NAME, UI_FUNCTIONS
from log import log_event_handler
from superset_api import SupersetApiClient, SupersetApiError, TrinoConnection

logger = logging.getLogger(__name__)


class TrinoCatalogRelationHandler(ops.Object):
    """Client for the superset:trino-catalog relation.

    Observes relation lifecycle events and synchronises Trino catalogs
    as Superset database connections.  Only active when the charm is
    running a UI function.
    """

    def __init__(self, charm: ops.CharmBase):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "trino-catalog")
        self.charm = charm

        self.trino_catalog_requirer = TrinoCatalogRequirer(
            self.charm, relation_name=TRINO_CATALOG_RELATION_NAME
        )

        self.framework.observe(
            charm.on[TRINO_CATALOG_RELATION_NAME].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[TRINO_CATALOG_RELATION_NAME].relation_broken,
            self._on_relation_broken,
        )
        self.framework.observe(
            charm.on.secret_changed,
            self._on_secret_changed,
        )
        self.framework.observe(
            charm.on.update_status,
            self._on_update_status,
        )

    @log_event_handler(logger)
    def _on_relation_changed(self, event: ops.RelationEvent) -> None:
        """Handle trino-catalog relation changed.

        Args:
            event: The event triggered when the relation changed.
        """
        self.sync_databases()

    @log_event_handler(logger)
    def _on_relation_broken(self, event: ops.RelationEvent) -> None:
        """Handle trino-catalog relation broken.

        Args:
            event: The event triggered when the relation departs.
        """
        logger.info(
            "Trino catalog relation broken (id=%s). "
            "Existing Superset databases are left intact.",
            event.relation.id,
        )

    @log_event_handler(logger)
    def _on_secret_changed(self, event: ops.SecretChangedEvent) -> None:
        """Handle secret-changed for Trino credential rotation.

        Only triggers sync when the changed secret matches the
        Trino credentials secret from the relation data.

        Args:
            event: The event triggered when a secret changes.
        """
        trino_info = self.trino_catalog_requirer.get_trino_info()
        if not trino_info:
            return

        secret_id = trino_info.get("trino_credentials_secret_id")
        if secret_id and event.secret.id == secret_id:
            logger.info("Trino credentials secret changed, syncing databases")
            self.sync_databases(force_update_credentials=True)

    @log_event_handler(logger)
    def _on_update_status(self, event: ops.UpdateStatusEvent) -> None:
        """Trigger database sync on update-status to reconcile state."""
        self.sync_databases()

    def sync_databases(self, force_update_credentials: bool = False) -> None:
        """Synchronise Trino catalogs into Superset database connections.

        For each Trino catalog, the connection is created if missing,
        updated if the host:port has changed, or left as-is.  When
        force_update_credentials is True, every existing connection
        is updated unconditionally (used in case of credential rotation).

        Args:
            force_update_credentials: If True, update all existing connections
        """
        if not self._should_sync():
            return

        # Gather required data
        sync_config = self._prepare_sync_config()
        if sync_config is None:
            return

        # Initialize API client and get existing databases
        api = self._create_api_client()
        if api is None:
            return

        try:
            existing_dbs = api.get_trino_databases()
        except SupersetApiError as e:
            logger.error("Failed to fetch existing databases: %s", e)
            return

        # Resolve target role for permission grants
        role_id = self._resolve_role_id(api)

        # Sync each catalog
        self._sync_catalogs(
            api=api,
            catalogs=sync_config["catalogs"],
            trino_url=sync_config["trino_url"],
            username=sync_config["username"],
            password=sync_config["password"],
            use_ssl=sync_config["use_ssl"],
            existing_dbs=existing_dbs,
            role_id=role_id,
            force_update=force_update_credentials,
        )

    def _should_sync(self) -> bool:
        """Check whether this unit should perform database sync.

        Returns:
            True if the charm function is a UI function and base
            relations are ready.
        """
        if not self.charm.unit.is_leader():
            logger.debug("Skipping trino-catalog sync: not the leader unit")
            return False

        if self.charm.config["charm-function"] not in UI_FUNCTIONS:
            logger.debug(
                "Skipping trino-catalog sync: charm-function '%s' "
                "is not a UI function",
                self.charm.config["charm-function"],
            )
            return False

        if not self.charm.ready_to_start():
            return False

        return True

    def _get_credentials(self) -> tuple[str, str] | tuple[None, None]:
        """Retrieve Trino credentials from the Juju secret.

        Returns:
            Tuple of (username, password), or (None, None) on failure.
        """
        try:
            credentials = self.trino_catalog_requirer.get_credentials()
        except ops.SecretNotFoundError:
            logger.error(
                "Trino credentials secret not found. "
                "Run: juju grant-secret <secret> superset-k8s"
            )
            return None, None
        except ops.ModelError as e:
            logger.error(
                "Permission denied accessing Trino credentials: %s. "
                "Run: juju grant-secret <secret> superset-k8s",
                e,
            )
            return None, None

        if not credentials:
            logger.error(
                "User 'app-superset-k8s' not found in Trino credentials. "
                "Add this user to the user-secret."
            )
            return None, None

        return credentials

    def _prepare_sync_config(self) -> dict[str, Any] | None:
        """Prepare configuration needed for database sync.

        Returns:
            Dict with sync config, or None if prerequisites not met.
        """
        trino_info = self.trino_catalog_requirer.get_trino_info()
        if not trino_info:
            logger.debug("No Trino catalog info available yet")
            return None

        username, password = self._get_credentials()
        if username is None:
            logger.warning("Missing Trino credentials, cannot sync databases")
            return None

        trino_url = trino_info["trino_url"]
        catalogs = trino_info["trino_catalogs"]
        use_ssl = self._use_ssl(trino_url)

        if not catalogs:
            logger.debug("No Trino catalogs to sync")
            return None

        return {
            "catalogs": catalogs,
            "trino_url": trino_url,
            "username": username,
            "password": password,
            "use_ssl": use_ssl,
        }

    def _use_ssl(self, trino_url: str) -> bool:
        """Determine whether SSL should be used from the Trino URL port.

        Args:
            trino_url: Trino URL in host:port format.

        Returns:
            True when the port is 443, False otherwise.
        """
        if ":" in trino_url:
            port = trino_url.rsplit(":", 1)[-1]
            return port == "443"
        return False

    def _catalog_display_name(self, catalog_name: str) -> str:
        """Generate Superset database name from a catalog.

        Example: google_ads -> "Google Ads (google_ads)".

        Args:
            catalog_name: Raw Trino catalog name (e.g. google_ads).

        Returns:
            Display name for the Superset database connection.
        """
        title = catalog_name.replace("_", " ").title()
        return f"{title} ({catalog_name})"

    def _create_api_client(self) -> SupersetApiClient | None:
        """Create and authenticate Superset API client.

        Returns:
            Authenticated API client, or None on failure.
        """
        try:
            return SupersetApiClient(
                admin_username="admin",
                admin_password=self.charm.config["admin-password"],
            )
        except SupersetApiError as e:
            logger.error("Superset API unavailable, skipping sync: %s", e)
            return None

    def _resolve_role_id(self, api: SupersetApiClient) -> int | None:
        """Resolve the role ID for permission grants.

        Args:
            api: Authenticated Superset API client.

        Returns:
            Role ID if found, None otherwise.
        """
        role_name = str(self.charm.config["self-registration-role"])
        try:
            role_id = api.get_role_id(role_name)
        except SupersetApiError as e:
            logger.error(
                "Failed to lookup role '%s': %s. Databases will be created "
                "but database_access will not be granted",
                role_name,
                e,
            )
            return None

        if role_id is None:
            logger.warning(
                "Could not resolve role '%s'; databases will be created "
                "but database_access will not be granted",
                role_name,
            )

        return role_id

    def _sync_catalogs(  # pylint: disable=too-many-positional-arguments
        self,
        api: SupersetApiClient,
        catalogs: list[TrinoCatalog],
        trino_url: str,
        username: str,
        password: str,
        use_ssl: bool,
        existing_dbs: list[TrinoConnection],
        role_id: int | None,
        force_update: bool,
    ) -> None:
        """Sync all catalogs to Superset.

        Args:
            api: Authenticated Superset API client.
            catalogs: List of Trino catalog objects.
            trino_url: Trino server URL (host:port).
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to use SSL.
            existing_dbs: List of existing Trino connections in Superset.
            role_id: Role ID for permission grants, or None.
            force_update: Whether to force update all connections.
        """
        for catalog in catalogs:
            db_name = self._catalog_display_name(catalog.name)
            existing_connections = [
                conn for conn in existing_dbs if conn.catalog == catalog.name
            ]

            if existing_connections:
                self._update_existing_connections(
                    api=api,
                    connections=existing_connections,
                    catalog_name=catalog.name,
                    trino_url=trino_url,
                    username=username,
                    password=password,
                    use_ssl=use_ssl,
                    force_update=force_update,
                )
            else:
                self._create_new_connection(
                    api=api,
                    db_name=db_name,
                    catalog_name=catalog.name,
                    trino_url=trino_url,
                    username=username,
                    password=password,
                    use_ssl=use_ssl,
                )

                self._grant_database_access(api, db_name, role_id)

    def _update_existing_connections(  # pylint: disable=too-many-positional-arguments
        self,
        api: SupersetApiClient,
        connections: list[TrinoConnection],
        catalog_name: str,
        trino_url: str,
        username: str,
        password: str,
        use_ssl: bool,
        force_update: bool,
    ) -> None:
        """Update existing database connections for a catalog.

        Args:
            api: Authenticated Superset API client.
            connections: Existing connections for this catalog.
            catalog_name: Trino catalog name.
            trino_url: Trino server URL (host:port).
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to use SSL.
            force_update: Whether to force update all connections.
        """
        for conn in connections:
            needs_update = (
                force_update or f"@{trino_url}/" not in conn.sqlalchemy_uri
            )

            if needs_update:
                logger.info(
                    "Updating Superset database '%s' (id=%s)",
                    conn.database_name,
                    conn.id,
                )
                try:
                    api.update_trino_database(
                        database_id=conn.id,
                        host_port=trino_url,
                        trino_catalog=catalog_name,
                        username=username,
                        password=password,
                        use_ssl=use_ssl,
                    )
                except SupersetApiError as e:
                    logger.error(
                        "Failed to update database '%s': %s",
                        conn.database_name,
                        e,
                    )
            else:
                logger.debug(
                    "Superset database '%s' is up to date, skipping",
                    conn.database_name,
                )

    def _create_new_connection(  # pylint: disable=too-many-positional-arguments
        self,
        api: SupersetApiClient,
        db_name: str,
        catalog_name: str,
        trino_url: str,
        username: str,
        password: str,
        use_ssl: bool,
    ) -> None:
        """Create a new database connection for a catalog.

        Args:
            api: Authenticated Superset API client.
            db_name: Display name for the database.
            catalog_name: Trino catalog name.
            trino_url: Trino server URL (host:port).
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to use SSL.
        """
        try:
            api.create_trino_database(
                database_name=db_name,
                trino_catalog=catalog_name,
                host_port=trino_url,
                username=username,
                password=password,
                use_ssl=use_ssl,
            )
        except SupersetApiError as e:
            logger.error(
                "Failed to create database '%s' for catalog '%s': %s",
                db_name,
                catalog_name,
                e,
            )

    def _grant_database_access(
        self,
        api: SupersetApiClient,
        db_name: str,
        role_id: int | None,
    ) -> None:
        """Grant database_access permission to the configured role.

        Args:
            api: Authenticated Superset API client.
            db_name: Database name in Superset.
            role_id: Role ID to grant permission to, or None to skip.
        """
        if role_id is None:
            return

        try:
            perm_id = api.get_database_access_permission_id(db_name)
        except SupersetApiError as e:
            logger.error(
                "Failed to lookup database_access permission for '%s': %s",
                db_name,
                e,
            )
            return

        if perm_id is None:
            logger.warning(
                "database_access permission for '%s' not yet available",
                db_name,
            )
            return

        try:
            api.update_role_permissions(role_id, perm_id)
        except SupersetApiError as e:
            logger.error(
                "Failed to grant database_access for '%s': %s", db_name, e
            )
