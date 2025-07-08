# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging
from typing import Dict, Optional

from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from ops import framework
from ops.charm import RelationEvent

from literals import DB_NAME
from log import log_event_handler

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
        self.charm.postgresql_db = DatabaseRequires(
            self.charm,
            relation_name="postgresql_db",
            database_name=DB_NAME,
            extra_user_roles="admin",
        )
        self.framework.observe(
            charm.postgresql_db.on.database_created, self._on_database_changed
        )
        self.framework.observe(
            charm.postgresql_db.on.endpoints_changed, self._on_database_changed
        )
        self.framework.observe(
            charm.on.postgresql_db_relation_changed, self._on_database_changed
        )
        self.framework.observe(
            charm.on.postgresql_db_relation_broken,
            self._on_database_relation_broken,
        )

    @log_event_handler(logger)
    def _on_database_changed(self, event: RelationEvent) -> None:
        """Handle database changed event.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        logger.info("handling %s change event", event.relation.name)

        dbconn = self._get_db_info()
        if dbconn is None:
            raise ValueError("database relation not ready")

        host = dbconn["host"]
        port = dbconn["port"]
        user = dbconn["user"]
        password = dbconn["password"]
        db_name = DB_NAME

        sqlalchemy_url = (
            f"postgresql://{user}:{password}@{host}:{port}/{db_name}"
        )
        self.charm._state.sql_alchemy_uri = sqlalchemy_url
        self.charm._state.postgresql_relation = "enabled"

        self.charm._update(event)

    @log_event_handler(logger)
    def _on_database_relation_broken(self, event):
        """Handle database broken event.

        Args:
            event: The event triggered when the relation departs.
        """
        if not self.charm.unit.is_leader():
            return

        container = self.charm.model.unit.get_container(self.charm.name)
        if not container.can_connect():
            event.defer()
            return

        self.charm._state.sql_alchemy_uri = None
        self.charm._state.postgresql_relation = False

        self.charm._update(event)

    def _get_db_info(self) -> Optional[Dict]:
        """Get database connection info by reading relation data."""
        if (
            len(self.charm.postgresql_db.relations) == 0
            or not self.charm.postgresql_db.is_resource_created()
        ):
            return None

        db_relation_id = self.charm.postgresql_db.relations[0].id
        relation_data = self.charm.postgresql_db.fetch_relation_data().get(
            db_relation_id, None
        )
        if not relation_data:
            return None

        host, port = relation_data.get("endpoints").split(",")[0].split(":")
        logger.info("database host: %s, port: %s", host, port)
        return {
            "host": host,
            "port": port,
            "password": relation_data.get("password"),
            "user": relation_data.get("username"),
        }
