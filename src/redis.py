# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging

from ops import framework
from log import log_event_handler
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents
from ops.framework import StoredState

logger = logging.getLogger(__name__)


class Redis(framework.Object):
    """Client for superset:redis relation."""

    on = RedisRelationCharmEvents()
    _stored = StoredState()

    def __init__(self, charm):
        """Construct.
        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "redis")
        self.charm = charm
        self.framework.observe(charm.on.redis_relation_updated, self._on_redis_relation_changed)

    @log_event_handler(logger)
    def _on_redis_relation_changed(self, event):
        """Handle redis relation updated event.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        if self.charm._stored.redis_relation:
            host, port = self._get_redis_relation_data()
            self.charm._state.redis_relation = "enabled"
        else:
            host = None
            port = None
            self.charm._state.redis_relation = "disabled"

        self.charm._state.redis_host = host
        self.charm._state.redis_port = port

        self.charm._update(event)

    def _get_redis_relation_data(self):
        """Get the hostname and port from the redis relation data.
        This is the current recommended way of accessing the relation data.

        Returns:
            redis_hostname: hostname of redis service
            redis_port: port of redis service
        """        
        for redis_unit in self.charm._stored.redis_relation: 
            redis_unit_data = self.charm._stored.redis_relation[redis_unit] 
            redis_hostname = redis_unit_data["hostname"]
            redis_port = redis_unit_data["port"]

        return redis_hostname, redis_port
