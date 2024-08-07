# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseRequires,
)
from ops import framework

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
            database_name="superset",
            extra_user_roles="admin",
        )
        self.framework.observe(
            charm.postgresql_db.on.database_created, self._on_database_changed
        )
        self.framework.observe(
            charm.postgresql_db.on.endpoints_changed, self._on_database_changed
        )
        self.framework.observe(
            charm.on.postgresql_db_relation_broken,
            self._on_database_relation_broken,
        )

    @log_event_handler(logger)
    def _on_database_changed(self, event: DatabaseCreatedEvent) -> None:
        """Handle database changed event.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        if not event.endpoints:
            return

        host, port = event.endpoints.split(",", 1)[0].split(":")
        user = event.username
        password = event.password
        db_name = event.database

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
