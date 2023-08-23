#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging

from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents, RedisRequires
from ops.charm import CharmBase, ConfigChangedEvent, PebbleReadyEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import CheckStatus

from literals import (
    APP_NAME,
    APPLICATION_PORT,
    UI_FUNCTIONS,
    VALID_CHARM_FUNCTIONS,
)
from log import log_event_handler
from relations.postgresql import Database
from relations.redis import Redis
from state import State
from utils import generate_random_string, load_superset_files

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class SupersetK8SCharm(CharmBase):
    """Charm the service.

    Attrs:
        _state: used to store data that is persisted across invocations.
        external_hostname: DNS listing used for external connections.
        on: redis relation events from redis_k8s library
        _stored: charm stored state
    """

    on = RedisRelationCharmEvents()
    _stored = StoredState()

    @property
    def external_hostname(self):
        """Return the DNS listing used for external connections."""
        return self.config["external-hostname"] or self.app.name

    def __init__(self, *args):
        """Construct.

        Args:
            args: Ignore.
        """
        super().__init__(*args)
        self.name = APP_NAME
        self._state = State(self.app, lambda: self.model.get_relation("peer"))

        # Handle postgresql relation
        self.postgresql_db = DatabaseRequires(
            self, relation_name="postgresql_db", database_name="superset"
        )
        self.database = Database(self)

        # Handle redis relation
        self._stored.set_default(redis_relation={})
        self.redis = RedisRequires(self, self._stored)
        self.redis_relation = Redis(self)

        # Handle basic charm lifecycle
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.on.superset_pebble_ready, self._on_pebble_ready
        )
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(self.on.update_status, self._on_update_status)

        # Handle Ingress
        self._require_nginx_route()

    def _require_nginx_route(self):
        """Require nginx-route relation based on current configuration."""
        require_nginx_route(
            charm=self,
            service_hostname=self.external_hostname,
            service_name=self.app.name,
            service_port=APPLICATION_PORT,
            tls_secret_name=self.config["tls-secret-name"],
            backend_protocol="HTTPS",
        )

    @log_event_handler(logger)
    def _on_install(self, event):
        """Install application.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = MaintenanceStatus(f"installing {APP_NAME}")

    @log_event_handler(logger)
    def _on_pebble_ready(self, event: PebbleReadyEvent):
        """Define and start a workload using the Pebble API.

        Args:
            event: The event triggered when the relation changed.
        """
        self._update(event)

    @log_event_handler(logger)
    def _on_config_changed(self, event: ConfigChangedEvent):
        """Handle changed configuration.

        Args:
            event: The event triggered when the relation changed.
        """
        self.unit.status = WaitingStatus(f"configuring {APP_NAME}")
        self._update(event)

    @log_event_handler(logger)
    def _on_update_status(self, event):
        """Handle `update-status` events.

        Args:
            event: The `update-status` event triggered at intervals
        """
        if not self.ready_to_start():
            return

        container = self.unit.get_container(self.name)

        if self.config["charm-function"] in UI_FUNCTIONS:
            check = container.get_check("up")
            if check.status != CheckStatus.UP:
                self.unit.status = MaintenanceStatus("Status check: DOWN")
                return

        self.unit.status = ActiveStatus("Status check: UP")

    def _restart_application(self, container):
        """Restart application.

        Args:
            container: application container
        """
        self.unit.status = MaintenanceStatus(f"restarting {APP_NAME}")
        container.restart(self.name)

    def ready_to_start(self):
        """Check if peer relation is established.

        Returns:
            True if peer relation established, else False.
        """
        if not self._state.is_ready():
            self.unit.status = WaitingStatus("Waiting for peer relation.")
            return False

        if not self._state.postgresql_relation:
            self.unit.status = BlockedStatus("Needs a PostgreSQL relation")
            return False

        if not self._state.redis_relation:
            self.unit.status = BlockedStatus("Needs a Redis relation")
            return False
        return True

    @log_event_handler(logger)
    def _on_restart(self, event):
        """Restart application, action handler.

        Args:
            event:The event triggered by the restart action
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        self.unit.status = MaintenanceStatus(f"restarting {APP_NAME}")
        self._restart_application(container)

        event.set_results({"result": f"{APP_NAME} successfully restarted"})

    def _validate_config_params(self):
        """Validate that configuration is valid.

        Raises:
            ValueError: in case when invalid charm-funcion is entered
        """
        charm_function = self.model.config["charm-function"]
        if charm_function not in VALID_CHARM_FUNCTIONS:
            raise ValueError(
                f"config: invalid charm function {charm_function!r}"
            )

    def _create_env(self):
        """Create state values from config to be used as environment variables.

        Returns:
            env: dictionary of environment variables
        """
        superset_secret = self.config.get(
            "superset-secret-key"
        ) or generate_random_string(32)
        charm_function = self.config["charm-function"]
        random_id = generate_random_string(5)
        env = {
            "SUPERSET_SECRET_KEY": superset_secret,
            "ADMIN_PASSWORD": self.config["admin-password"],
            "CHARM_FUNCTION": charm_function,
            "SQL_ALCHEMY_URI": self._state.sql_alchemy_uri,
            "REDIS_HOST": self._state.redis_host,
            "REDIS_PORT": self._state.redis_port,
            "ADMIN_USER": f"{charm_function}-{random_id}",
            'ALERTS_ATTACH_REPORTS': self.config["alerts-attach-reports"],
            'DASHBOARD_CROSS_FILTERS': self.config["dashboard-cross-filters"],
            'DASHBOARD_RBAC': self.config["dashboard-rbac"],
            'EMBEDDABLE_CHARTS': self.config["embeddable-charts"],
            'SCHEDULED_QUERIES': self.config["scheduled-queries"],
            'ESTIMATE_QUERY_COST': self.config["estimate-query-cost"],
            'ENABLE_TEMPLATE_PROCESSING': self.config["enable-template-processing"],
            'ALERT_REPORTS': self.config["alert-reports"],
            'SQLALCHEMY_POOL_SIZE': self.config["sqlalchemy-pool-size"],
            'SQLALCHEMY_POOL_TIMEOUT': self.config["sqlalchemy-pool-timeout"],
            'SQLALCHEMY_MAX_OVERFLOW': self.config["sqlalchemy-max-overflow"],

        }
        return env

    def _update(self, event):
        """Update the application server configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.defer()
            return

        if not self.ready_to_start():
            return

        try:
            self._validate_config_params()
        except (RuntimeError, ValueError) as err:
            self.unit.status = BlockedStatus(str(err))
            return

        logger.info(f"configuring {APP_NAME}")
        env = self._create_env()
        load_superset_files(container)

        logger.info(f"planning {APP_NAME} execution")
        pebble_layer = {
            "summary": f"{APP_NAME} layer",
            "description": f"pebble config layer for {APP_NAME}",
            "services": {
                self.name: {
                    "override": "replace",
                    "summary": f"{APP_NAME} server",
                    "command": "/app/k8s/k8s-bootstrap.sh",
                    "startup": "enabled",
                    "environment": env,
                    "on-check-failure": {"up": "ignore"},
                }
            },
        }
        if self.config["charm-function"] in UI_FUNCTIONS:
            pebble_layer.update(
                {
                    "checks": {
                        "up": {
                            "override": "replace",
                            "period": "10s",
                            "http": {"url": "http://localhost:8088/"},
                        }
                    }
                },
            )
        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()
        self.unit.status = MaintenanceStatus("replanning application")


if __name__ == "__main__":
    main(SupersetK8SCharm)
