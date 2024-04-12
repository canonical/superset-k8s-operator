# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Defines statsd relation event handling methods."""

import logging

from ops import CharmBase, framework
from ops.model import WaitingStatus

from log import log_event_handler

logger = logging.getLogger(__name__)


class StatsDRelationHandler(framework.Object):
    """Client for statsd-exporter relation."""

    def __init__(
        self, charm: CharmBase, relation_name: str = "statsd-exporter"
    ):
        """Construct StatsDRelationHandler object.

        Args:
            charm: the charm for which this relation is provided
            relation_name: the name of the relation
        """
        self.relation_name = relation_name

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_changed,
            self._on_relation_changed,
        )

        self.framework.observe(
            charm.on[self.relation_name].relation_broken,
            self._on_relation_broken,
        )

        self.charm = charm

    @log_event_handler(logger)
    def _on_relation_changed(self, event):
        """Handle statsd_exporter change events.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        self.charm.unit.status = WaitingStatus(
            "handling statsd relation change"
        )
        self.update(event)

    @log_event_handler(logger)
    def _on_relation_broken(self, event) -> None:
        """Handle broken relations with statsd_exporter.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm._state.is_ready():
            event.defer()
            return

        if self.charm.unit.is_leader():
            self.update(event, True)

    def update(self, event, relation_broken=False):
        """Assign nested value in peer relation.

        Args:
            event: The event triggered when the relation changed.
            relation_broken: true if database connection is broken.
        """
        for key in ["statsd_host", "statsd_port"]:
            value = None
            if not relation_broken:
                value = event.relation.data[event.app].get(key)
            setattr(self.charm._state, key, value)
        self.charm._update(event)
