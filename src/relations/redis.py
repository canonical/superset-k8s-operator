# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging

from charms.redis_k8s.v0.redis import RedisRequires
from ops import framework

from log import log_event_handler

logger = logging.getLogger(__name__)


class Redis(framework.Object):
    """Client for superset:redis relation."""

    def __init__(self, charm):
        """Construct.

        Args:
            charm: The charm to attach the hooks to.
        """
        super().__init__(charm, "redis")
        self.charm = charm
        self.charm.redis = RedisRequires(charm)
        self.framework.observe(
            charm.on.redis_relation_updated, self._on_redis_relation_changed
        )

    @log_event_handler(logger)
    def _on_redis_relation_changed(self, event):
        """Handle redis relation updated event.

        Args:
            event: The event triggered when the relation changed.
        """
        if not self.charm.unit.is_leader():
            return

        host, port, redis_relation = self._get_redis_relation_data()

        self.charm._state.redis_relation = redis_relation
        self.charm._state.redis_host = host
        self.charm._state.redis_port = port

        self.charm._update(event)

    def _get_redis_relation_data(self):
        """Get the hostname and port from the redis relation data.

        Returns:
            redis_hostname: hostname of redis service
            redis_port: port of redis service
            redis_relation: bool for if redis has been related
        """
        unit_data = self.charm.redis.relation_data or {}
        relation = self.model.get_relation("redis")
        application_data = relation.data[relation.app] if relation else {}

        redis_hostname = application_data.get("leader-host") or unit_data.get(
            "hostname"
        )
        redis_port = unit_data.get("port")
        redis_relation = bool(unit_data)
        return redis_hostname, redis_port, redis_relation
