# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Define the Superset server Postgresql relation."""

import logging

from charms.redis_k8s.v0.redis import RedisRequires
from ops import framework

from literals import REDIS_RELATION_NAME
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
        self.charm._update(event)

    def get_redis_relation_data(self):
        """Get the hostname and port from the redis relation data.

        Returns:
            redis_hostname: hostname of redis service
            redis_port: port of redis service
        """
        if self.charm.model.get_relation(REDIS_RELATION_NAME) is None:
            logger.debug("no redis relation found")
            return None, None

        unit_data = self.charm.redis.relation_data or {}
        relation = self.model.get_relation(REDIS_RELATION_NAME)
        application_data = relation.data[relation.app] if relation else {}

        redis_hostname = application_data.get("leader-host") or unit_data.get(
            "hostname"
        )
        redis_port = unit_data.get("port")
        return redis_hostname, redis_port
