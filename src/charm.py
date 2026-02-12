#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

https://discourse.charmhub.io/t/4208
"""

import logging
import os

from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.nginx_ingress_integrator.v0.nginx_route import require_nginx_route
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents
from ops import ModelError, SecretNotFoundError, pebble
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
    DB_NAME,
    DB_RELATION_NAME,
    DEFAULT_ROLES,
    LOG_FILE,
    PROMETHEUS_METRICS_PORT,
    REDIS_RELATION_NAME,
    SQL_AB_ROLE,
    STATSD_PORT,
    SUPERSET_VERSION,
    TRINO_CATALOG_RELATION_NAME,
    UI_FUNCTIONS,
)
from log import log_event_handler
from relations.postgresql import Database
from relations.redis import Redis
from relations.trino_catalog import TrinoCatalogRelationHandler
from structured_config import CharmConfig
from utils import load_superset_files, query_metadata_database

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class SupersetK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attrs:
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

        # Handle postgresql relation
        self.database = Database(self)

        # Handle redis relation
        self.redis_handler = Redis(self)

        # Handle trino-catalog relation
        self.trino_catalog_handler = TrinoCatalogRelationHandler(self)

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
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)

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
        self.unit.status = WaitingStatus(f"configuring {APP_NAME}")
        self._update(event)

    @log_event_handler(logger)
    def _on_secret_changed(self, event):
        """Handle secret changes.

        Args:
            event: The event triggered when the secret changed.
        """
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

        # Sync Trino catalog databases if the relation exists
        if self.model.get_relation(TRINO_CATALOG_RELATION_NAME):
            self.trino_catalog_handler.sync_databases()

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

    def _validate_self_registration_role(self, sqlalchemy_uri: str):
        """Determine allowed Superset roles.

        Args:
            sqlalchemy_uri (str): the SQL Alchemy URI.

        Raises:
            ValueError: in case role value is not allowed.
        """
        sql = SQL_AB_ROLE

        allowed_roles = query_metadata_database(sqlalchemy_uri, sql)
        if not allowed_roles:
            allowed_roles = DEFAULT_ROLES
        role = self.config["self-registration-role"]
        if role not in allowed_roles:
            logger.error(
                "The self-registration role %s is not allowed. Use only %s.",
                role,
                allowed_roles,
            )
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
        if self.model.get_relation(DB_RELATION_NAME) is None:
            self.unit.status = BlockedStatus("Needs a PostgreSQL relation")
            return False

        if self.model.get_relation(REDIS_RELATION_NAME) is None:
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
            event.set_results({"error": "could not connect to container"})
            return

        self.unit.status = MaintenanceStatus(f"restarting {APP_NAME}")
        self._restart_application(container)

        event.set_results({"result": f"{APP_NAME} successfully restarted"})

    def _get_smtp_config(self):
        """Return SMTP variables."""
        ret = {}

        if not self.config["smtp-secret-id"]:
            return ret

        secret_id = self.config["smtp-secret-id"]

        try:
            secret = self.model.get_secret(id=secret_id)
            content = secret.get_content(refresh=True)
        except SecretNotFoundError as e:
            # Distinguish between a missing secret and an existing secret
            # that the charm has not been granted access to. The testing
            # backend raises SecretNotFoundError with a message containing
            # "not granted access" when the secret exists but is not
            # accessible to this charm.
            msg = str(e)
            if "not granted access" in msg:
                raise ValueError(
                    f"SMTP secret with ID '{secret_id}' cannot be accessed."
                ) from None
            raise ValueError(
                f"SMTP secret with ID '{secret_id}' cannot be found."
            ) from None
        except ModelError:
            raise ValueError(
                f"SMTP secret with ID '{secret_id}' cannot be accessed."
            ) from None

        required_keys = {
            "host",
            "port",
            "username",
            "password",
            "email",
            "ssl",
            "starttls",
            "ssl-server-auth",
            "superset-external-url",
        }

        missing_keys = []
        for key in required_keys:
            if key not in content:
                missing_keys.append(key)

        if missing_keys:
            raise ValueError(
                f"SMTP secret with ID '{secret_id}' has improper schema. Missing: {', '.join(missing_keys)}"
            )

        for key in required_keys:
            formatted_key = f"smtp_{key.replace('-', '_')}".upper()
            ret[formatted_key] = content[key]

        # Optional configurations
        ret["SMTP_EMAIL_SUBJECT_PREFIX"] = content.get(
            "email-subject-prefix", "[Superset] "
        )

        return ret

    def _create_env(self):
        """Create state values from config to be used as environment variables.

        Returns:
            env: dictionary of environment variables
        """
        database_relation_data = self.database.get_db_info()
        if database_relation_data is None:
            raise ValueError("database relation data is not available")

        sqlalchemy_uri = f"postgresql://{database_relation_data['user']}:{database_relation_data['password']}@{database_relation_data['host']}:{database_relation_data['port']}/{DB_NAME}"
        self._validate_self_registration_role(sqlalchemy_uri)

        (
            redis_hostname,
            redis_port,
        ) = self.redis_handler.get_redis_relation_data()
        if redis_hostname is None or redis_port is None:
            raise ValueError("redis relation data is not available")

        env = {
            "ALLOW_IMAGE_DOMAINS": self.config["allow-image-domains"],
            "SUPERSET_SECRET_KEY": self.config["superset-secret-key"],
            "ADMIN_PASSWORD": self.config["admin-password"],
            "CHARM_FUNCTION": self.config["charm-function"].value,
            "SQL_ALCHEMY_URI": sqlalchemy_uri,
            "REDIS_HOST": redis_hostname,
            "REDIS_PORT": redis_port,
            "SQLALCHEMY_POOL_SIZE": self.config["sqlalchemy-pool-size"],
            "SQLALCHEMY_POOL_TIMEOUT": self.config["sqlalchemy-pool-timeout"],
            "SQLALCHEMY_MAX_OVERFLOW": self.config["sqlalchemy-max-overflow"],
            "GOOGLE_KEY": self.config["google-client-id"],
            "GOOGLE_SECRET": self.config["google-client-secret"],
            "OAUTH_DOMAIN": self.config["oauth-domain"],
            "OAUTH_ADMIN_EMAIL": self.config["oauth-admin-email"],
            "SELF_REGISTRATION_ROLE": self.config["self-registration-role"],
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
            "REDIS_TIMEOUT": self.config["redis-timeout"],
            "DASHBOARD_SIZE_LIMIT": self.config["dashboard-size-limit"],
            "MAX_CONTENT_LENGTH": self.config["max-content-length"],
            "MAX_FORM_MEMORY_SIZE": self.config["max-form-memory-size"],
            "MAX_FORM_PARTS": self.config["max-form-parts"],
        }
        if self.config["feature-flags"]:
            env.update(self.config["feature-flags"])
        env.update(self._get_smtp_config())

        http_proxy = os.environ.get("JUJU_CHARM_HTTP_PROXY")
        https_proxy = os.environ.get("JUJU_CHARM_HTTPS_PROXY")
        no_proxy = os.environ.get("JUJU_CHARM_NO_PROXY")

        if http_proxy or https_proxy:
            env.update(
                {
                    "HTTP_PROXY": http_proxy,
                    "HTTPS_PROXY": https_proxy,
                    "NO_PROXY": no_proxy,
                }
            )

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
        try:
            env = self._create_env()
        except ValueError as e:
            self.unit.status = BlockedStatus(str(e))
            return

        load_superset_files(container)

        (
            redis_hostname,
            redis_port,
        ) = self.redis_handler.get_redis_relation_data()

        metrics_exporter_command = (
            f"/usr/bin/celery-exporter --broker-url redis://{redis_hostname}:{redis_port}/4 --port {PROMETHEUS_METRICS_PORT}"
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
