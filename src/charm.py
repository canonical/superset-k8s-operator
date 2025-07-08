#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging

from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents
from ops import pebble
from ops.charm import ConfigChangedEvent, PebbleReadyEvent
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
    CONFIG_PATH,
    DEFAULT_ROLES,
    LOG_FILE,
    PROMETHEUS_METRICS_PORT,
    SQL_AB_ROLE,
    STATSD_PORT,
    SUPERSET_VERSION,
    UI_FUNCTIONS,
)
from log import log_event_handler
from relations.postgresql import Database
from relations.redis import Redis
from state import State
from structured_config import CharmConfig
from utils import load_superset_files, query_metadata_database, random_string

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class SupersetK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attrs:
        _state: used to store data that is persisted across invocations.
        external_hostname: DNS listing used for external connections.
        on: redis relation events from redis_k8s library
        config_type: the charm structured config
    """

    config_type = CharmConfig
    on = RedisRelationCharmEvents()

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
        self.database = Database(self)

        # Handle redis relation
        self.redis_handler = Redis(self)

        # Handle basic charm lifecycle
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(
            self.on.superset_pebble_ready, self._on_pebble_ready
        )
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(
            self.on.peer_relation_changed, self._on_peer_relation_changed
        )

        # Handle Ingress
        self._require_nginx_route()

        # Loki
        self._log_forwarder = LogForwarder(self, relation_name="logging")

        # Grafana
        self._grafana_dashboards = GrafanaDashboardProvider(
            self, relation_name="grafana-dashboard"
        )

        # Prometheus
        self._prometheus_scraping = MetricsEndpointProvider(
            self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "static_configs": [
                        {"targets": [f"*:{PROMETHEUS_METRICS_PORT}"]}
                    ]
                }
            ],
            refresh_event=self.on.config_changed,
        )

    def _require_nginx_route(self):
        """Require nginx-route relation based on current configuration."""
        require_nginx_route(
            charm=self,
            service_hostname=self.external_hostname,
            service_name=self.app.name,
            service_port=APPLICATION_PORT,
            tls_secret_name=self.config["tls-secret-name"] or "",
            backend_protocol="HTTP",
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
    def _on_peer_relation_changed(self, event):
        """Handle peer relation changes.

        Args:
            event: The event triggered when the peer relation changed.
        """
        if self.unit.is_leader():
            return

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
        valid_pebble_plan = self._validate_pebble_plan(container)
        if not valid_pebble_plan:
            self._update(event)
            return

        if self.config["charm-function"] in UI_FUNCTIONS:
            check = container.get_check("up")
            if check.status != CheckStatus.UP:
                self.unit.status = MaintenanceStatus("Status check: DOWN")
                return

        self.unit.set_workload_version(f"v{SUPERSET_VERSION}")
        self.unit.status = ActiveStatus("Status check: UP")

    def _validate_pebble_plan(self, container):
        """Validate Superset pebble plan.

        Args:
            container: application container

        Returns:
            bool of pebble plan validity
        """
        try:
            plan = container.get_plan().to_dict()
            return bool(
                plan
                and plan["services"].get(self.name, {}).get("on-check-failure")
            )
        except pebble.ConnectionError:
            return False

    def _validate_self_registration_role(self):
        """Determine allowed Superset roles.

        Raises:
            ValueError: in case role value is not allowed.
        """
        uri = self._state.sql_alchemy_uri
        sql = SQL_AB_ROLE

        allowed_roles = query_metadata_database(uri, sql)
        if not allowed_roles:
            allowed_roles = DEFAULT_ROLES
        role = self.config["self-registration-role"]
        if role not in allowed_roles:
            raise ValueError(
                f"The self-registration role {role} is not allowed. Use only {allowed_roles}."
            )

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

    def _handle_superset_secret(self):
        """Set superset secret in _state."""
        if not self.unit.is_leader():
            return

        if self.config["superset-secret-key"]:
            superset_secret = self.config["superset-secret-key"]
        else:
            superset_secret = self._state.secret_key or random_string(32)
        self._state.superset_secret_key = superset_secret

    def _create_env(self):
        """Create state values from config to be used as environment variables.

        Returns:
            env: dictionary of environment variables
        """
        self._handle_superset_secret()
        self._validate_self_registration_role()

        env = {
            "ALLOW_IMAGE_DOMAINS": self.config["allow-image-domains"],
            "SUPERSET_SECRET_KEY": self._state.superset_secret_key,
            "ADMIN_PASSWORD": self.config["admin-password"],
            "CHARM_FUNCTION": self.config["charm-function"].value,
            "SQL_ALCHEMY_URI": self._state.sql_alchemy_uri,
            "REDIS_HOST": self._state.redis_host,
            "REDIS_PORT": self._state.redis_port,
            "SQLALCHEMY_POOL_SIZE": self.config["sqlalchemy-pool-size"],
            "SQLALCHEMY_POOL_TIMEOUT": self.config["sqlalchemy-pool-timeout"],
            "SQLALCHEMY_MAX_OVERFLOW": self.config["sqlalchemy-max-overflow"],
            "GOOGLE_KEY": self.config["google-client-id"],
            "GOOGLE_SECRET": self.config["google-client-secret"],
            "OAUTH_DOMAIN": self.config["oauth-domain"],
            "OAUTH_ADMIN_EMAIL": self.config["oauth-admin-email"],
            "SELF_REGISTRATION_ROLE": self.config["self-registration-role"],
            "HTTP_PROXY": self.config["http-proxy"],
            "HTTPS_PROXY": self.config["https-proxy"],
            "NO_PROXY": self.config["no-proxy"],
            "SUPERSET_LOAD_EXAMPLES": self.config["load-examples"],
            "PYTHONPATH": CONFIG_PATH,
            "HTML_SANITIZATION": self.config["html-sanitization"],
            "HTML_SANITIZATION_SCHEMA_EXTENSIONS": self.config[
                "html-sanitization-schema-extensions"
            ],
            "GLOBAL_ASYNC_QUERIES_JWT": self.config[
                "global-async-queries-jwt"
            ],
            "GLOBAL_ASYNC_QUERIES_POLLING_DELAY": self.config[
                "global-async-queries-polling-delay"
            ],
            "SENTRY_DSN": self.config["sentry-dsn"],
            "SENTRY_RELEASE": self.config["sentry-release"],
            "SENTRY_ENVIRONMENT": self.config["sentry-environment"],
            "SENTRY_REDACT_PARAMS": self.config["sentry-redact-params"],
            "SENTRY_SAMPLE_RATE": self.config["sentry-sample-rate"],
            "SERVER_ALIAS": self.config["server-alias"],
            "APPLICATION_PORT": APPLICATION_PORT,
            "WEBSERVER_TIMEOUT": self.config["webserver-timeout"],
            "STATSD_PORT": STATSD_PORT,
            "LOG_FILE": LOG_FILE,
            "CACHE_WARMUP": self.config["cache-warmup"],
        }
        if self.config["feature-flags"]:
            env.update(self.config["feature-flags"])

        return env

    def _update(self, event):
        """Update the application server configuration and replan its execution.

        Args:
            event: The event triggered when the relation changed.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            return

        if not self.ready_to_start():
            return

        logger.info("configuring %s", APP_NAME)
        env = self._create_env()
        load_superset_files(container)

        metrics_exporter_command = (
            f"/usr/bin/celery-exporter --broker-url redis://{self._state.redis_host}:{self._state.redis_port}/4 --port {PROMETHEUS_METRICS_PORT}"
            if self.config["charm-function"] == "worker"
            else "/usr/bin/statsd_exporter"
        )

        logger.info("planning %s execution", APP_NAME)
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
                },
                "metrics-exporter": {
                    "override": "replace",
                    "summary": "metrics exporter",
                    "command": metrics_exporter_command,
                    "startup": "enabled",
                    "after": [self.name],
                },
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

            # Open port for cache warm-up.
            self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")

            # Open ports for accepting and exposing metrics
            self.model.unit.open_port(
                port=PROMETHEUS_METRICS_PORT, protocol="tcp"
            )
            self.model.unit.open_port(port=STATSD_PORT, protocol="udp")

        container.add_layer(self.name, pebble_layer, combine=True)
        container.replan()
        self.unit.status = MaintenanceStatus("replanning application")


if __name__ == "__main__":
    main(SupersetK8SCharm)
