# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging
from typing import Dict, Optional

from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from ops import framework
from ops.charm import RelationEvent

from literals import DB_NAME, DB_RELATION_NAME

logger = logging.getLogger(__name__)


class Database(framework.Object):
    """Client for superset:postgresql relation."""

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "database")
        self.charm = charm
        self.requirer = DatabaseRequires(
            self.charm,
            relation_name=DB_RELATION_NAME,
            database_name=DB_NAME,
            extra_user_roles="admin",
        )
        for event in (
            self.requirer.on.database_created,
            self.requirer.on.endpoints_changed,
            charm.on.postgresql_db_relation_changed,
            charm.on.postgresql_db_relation_broken,
        ):
            self.framework.observe(event, self._on_reconcile)

    def _on_reconcile(self, event: RelationEvent) -> None:
        """Re-apply the desired state when the relation changes.

        Args:
            event: The event triggered when the relation changed or departed.
        """
        self.charm.reconcile()

    def get_db_info(self) -> Optional[Dict]:
        """Get database connection info by reading relation data.

        Returns:
            Optional[Dict]: Information needed for setting up database connection.
        """
        if (
            self.charm.model.get_relation(DB_RELATION_NAME) is None
            or not self.requirer.is_resource_created()
        ):
            logger.debug(
                "no postgresql_db relation found or resource not created"
            )
            return None

        db_relation_id = self.requirer.relations[0].id
        relation_data = self.requirer.fetch_relation_data().get(
            db_relation_id, None
        )
        if not relation_data:
            logger.debug(
                "no relation data found for relation %s", db_relation_id
            )
            return None

        logger.debug(
            "postgresql_db endpoints: %s", relation_data.get("endpoints")
        )
        host, port = relation_data.get("endpoints").split(",")[0].split(":")
        logger.info("database host: %s, port: %s", host, port)
        return {
            "host": host,
            "port": port,
            "password": relation_data.get("password"),
            "user": relation_data.get("username"),
        }

    def get_db_uri(self) -> Optional[str]:
        """Build the SQLAlchemy URI for the Superset metadata database.

        Returns:
            Optional[str]: Full postgresql:// SQLAlchemy URI, or None if the
            relation data is not available.
        """
        db_info = self.get_db_info()
        if db_info is None:
            return None

        return (
            f"postgresql://{db_info['user']}:{db_info['password']}"
            f"@{db_info['host']}:{db_info['port']}/{DB_NAME}"
        )
