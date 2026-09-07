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
import secrets
from typing import Optional

import ops
import requests
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.redis_k8s.v0.redis import RedisRelationCharmEvents
from charms.traefik_k8s.v2.ingress import IngressPerAppRequirer
from ops import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    ModelError,
    SecretNotFoundError,
    WaitingStatus,
    pebble,
)
from pydantic import ValidationError

from literals import (
    ADMIN_SECRET_ID_FIELD,
    ADMIN_SECRET_KEY,
    ADMIN_SECRET_LABEL,
    APP_NAME,
    APPLICATION_PORT,
    CONFIG_PATH,
    DB_RELATION_NAME,
    HEALTH_PROBE_TIMEOUT,
    HEALTH_URL,
    INGRESS_RELATION_NAME,
    LOG_FILE,
    PROMETHEUS_METRICS_PORT,
    REDIS_RELATION_NAME,
    SIGNING_KEYS_SECRET_KEYS,
    SQL_AB_ROLE,
    STATSD_PORT,
    SUPERSET_VERSION,
    TRINO_CATALOG_RELATION_NAME,
    UI_FUNCTIONS,
)
from relations.oauth import ClientConfigError, OAuthRelation
from relations.postgresql import Database
from relations.redis import Redis
from relations.tls import CertificateInstallError, Certificates
from relations.trino_catalog import TrinoCatalogRelationHandler
from structured_config import CharmConfig
from utils import load_superset_files, query_metadata_database

# Log messages can be retrieved using juju debug-log
logger = logging.getLogger(__name__)


class SupersetK8SCharm(TypedCharmBase[CharmConfig]):
    """Charm the service.

    Attrs:
        https_ingress_url: external HTTPS URL published by the ingress provider
        on: redis relation events from redis_k8s library
        config_type: the charm structured config
    """

    config_type = CharmConfig
    on = RedisRelationCharmEvents()

    @property
    def https_ingress_url(self) -> Optional[str]:
        """Return the external HTTPS URL published by the ingress provider.

        Returns:
            The URL without any trailing slash, or None when no ingress
            provider has published one over HTTPS yet.
        """
        url = self.ingress.url
        if not url or not url.startswith("https://"):
            return None
        return url.rstrip("/")

    def __init__(self, framework: ops.Framework):
        """Construct.

        Args:
            framework: The ops framework.
        """
        super().__init__(framework)
        self.name = APP_NAME

        # Handle postgresql relation
        self.database = Database(self)

        # Handle redis relation
        self.redis_handler = Redis(self)

        # Handle trino-catalog relation
        self.trino_catalog_handler = TrinoCatalogRelationHandler(self)

        # Handle OAuth relation
        self.oauth = OAuthRelation(self)
        # Handle tls-certificates relation
        self.certificates_handler = Certificates(self)

        # The unit status is decided in one place, at the end of every
        # dispatch, from what the model and the workload actually say.
        self._reconcile_failure = None
        self.framework.observe(
            self.on.collect_unit_status, self._on_collect_unit_status
        )

        # Handle basic charm lifecycle
        self.framework.observe(self.on.restart_action, self._on_restart)
        self.framework.observe(
            self.on.get_admin_password_action, self._on_get_admin_password
        )
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.framework.observe(self.on.secret_changed, self._on_secret_changed)

        # Handle events that only have to re-apply the desired state.
        for event in (
            self.on.config_changed,
            self.on.peer_relation_changed,
            self.on.superset_pebble_ready,
            self.on.superset_pebble_check_failed,
            self.on.superset_pebble_check_recovered,
        ):
            self.framework.observe(event, self._on_reconcile)

        # Handle Ingress
        self.ingress = IngressPerAppRequirer(
            self,
            relation_name=INGRESS_RELATION_NAME,
            port=APPLICATION_PORT,
            scheme="http",
            strip_prefix=True,
            redirect_https=True,
        )
        self.framework.observe(self.ingress.on.ready, self._on_reconcile)
        self.framework.observe(self.ingress.on.revoked, self._on_reconcile)

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

    def _on_reconcile(self, event):
        """Re-apply the desired state.

        Args:
            event: The event that triggered the reconciliation.
        """
        self.reconcile()

    def _on_secret_changed(self, event):
        """Handle secret changes.

        Args:
            event: The event triggered when the secret changed.
        """
        self.reconcile(
            force_trino_credentials=(
                self.trino_catalog_handler.is_trino_credentials_secret(
                    event.secret
                )
            )
        )

    def _on_update_status(self, event):
        """Handle `update-status` events.

        The periodic hook is a plain reconcile: it re-applies the desired
        state, and the status that follows is collected from the workload
        like it is on any other event.

        Args:
            event: The `update-status` event triggered at intervals
        """
        self._refresh_ingress_address()
        self.reconcile()

    def _refresh_ingress_address(self):
        """Republish the unit's address on the ingress relation.

        `IngressPerAppRequirer` publishes the address on relation churn,
        `leader-elected` and `upgrade-charm` only so one bad read
        routes the ingress at a dead IP indefinitely. It is a no-op when
        the value has not changed.
        """
        self.ingress.provide_ingress_requirements(port=APPLICATION_PORT)

    def reconcile_certificates(self, relation_broken: bool = False):
        """Sync the workload CA trust store with the certificates relation.

        The trust store is filesystem state rather than Pebble plan state, so
        a replan is a no-op after a certificate change and the workload must
        be restarted explicitly to pick up the new material. Calling this from
        every reconcile point also re-installs the CA after a pod respawn has
        wiped the container filesystem.

        Args:
            relation_broken: Whether the certificates relation is being removed.

        Returns:
            True if the trust store is in the expected state, False if it
            could not be updated.
        """
        container = self.unit.get_container(self.name)
        if not container.can_connect():
            return True

        try:
            changed = self.certificates_handler.reconcile(
                container, relation_broken=relation_broken
            )
        except CertificateInstallError as e:
            logger.error("CA trust store update failed: %s", e)
            self.report_failure(BlockedStatus(str(e)))
            return False

        if changed and self.name in container.get_services():
            self._restart_application(container)

        return True

    def _validate_self_registration_role(self, sqlalchemy_uri: str):
        """Check the configured self-registration role exists in Superset.

        The roles live in the metadata database, which does not carry the
        `ab_role` table until Superset has migrated it. Until then, and
        whenever the database cannot answer, the role is left unvalidated.

        Args:
            sqlalchemy_uri (str): the SQL Alchemy URI.

        Raises:
            ValueError: in case role value is not allowed.
        """
        allowed_roles = query_metadata_database(sqlalchemy_uri, SQL_AB_ROLE)
        if not allowed_roles:
            logger.debug(
                "Superset roles are not readable yet, "
                "leaving self-registration-role unvalidated"
            )
            return

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
        container.restart(self.name)

    def _config_status(self):
        """Report on config the charm cannot be run with.

        Returns:
            The status to report, or None when the config is valid.
        """
        try:
            _ = self.config
            return None
        except ValidationError as e:
            missing = [
                str(err["loc"][0]).replace("_", "-")
                for err in e.errors()
                if err["type"] == "value_error.missing"
            ]
            return BlockedStatus(
                f"missing required config: {', '.join(missing)}"
                if missing
                else str(e)
            )

    @property
    def _peer_relation(self):
        """Return the peer relation, or None before it is established."""
        return self.model.get_relation("peer")

    def _signing_keys(self):
        """Return the contents of the signing keys secret.

        Returns:
            The secret content as a mapping.

        Raises:
            ValueError: When the option is unset, or the secret cannot be
                read, or it does not carry both keys.
        """
        secret_id = self.config["signing-keys-secret-id"]
        if not secret_id:
            raise ValueError("missing required config: signing-keys-secret-id")

        try:
            content = self.model.get_secret(id=secret_id).get_content(
                refresh=True
            )
        except SecretNotFoundError:
            raise ValueError(
                f"signing keys secret '{secret_id}' cannot be found."
            ) from None
        except ModelError:
            raise ValueError(
                f"signing keys secret '{secret_id}' cannot be accessed."
            ) from None

        missing = [
            key
            for key in SIGNING_KEYS_SECRET_KEYS
            if not content.get(key, "").strip()
        ]
        if missing:
            raise ValueError(
                f"signing keys secret '{secret_id}' has improper schema. "
                f"Missing: {', '.join(missing)}"
            )

        return content

    def admin_password(self):
        """Return the admin password.

        The secret is charm-owned, so it is created once by the leader and
        found by every unit through the ID published on the peer databag: a
        label lookup is not reliable from a hook other than the one that
        created the secret.

        Returns:
            The admin password, or None when the leader has not created the
            secret yet.
        """
        peer = self._peer_relation
        if peer is None:
            return None

        secret_id = peer.data[self.app].get(ADMIN_SECRET_ID_FIELD)
        if not secret_id:
            return None

        try:
            return self.model.get_secret(id=secret_id).get_content(
                refresh=True
            )[ADMIN_SECRET_KEY]
        except (SecretNotFoundError, ModelError, KeyError):
            logger.warning("admin password secret %s is unreadable", secret_id)
            return None

    def _ensure_admin_password(self):
        """Create the admin password secret on the leader if it is missing.

        A follower has nothing to do here: it waits for the ID to appear on
        the peer databag.
        """
        peer = self._peer_relation
        if peer is None or not self.unit.is_leader():
            return

        if peer.data[self.app].get(ADMIN_SECRET_ID_FIELD):
            return

        content = {ADMIN_SECRET_KEY: secrets.token_urlsafe(24)}
        secret = self.app.add_secret(content, label=ADMIN_SECRET_LABEL)
        peer.data[self.app][ADMIN_SECRET_ID_FIELD] = secret.id

    def _workload_status(self):
        """Return the status the workload presents right now.

        Pebble reports a service as started as soon as its process runs,
        which for the UI is up to a minute before Superset answers a request,
        and a check that keeps running across a restart carries the previous
        run's verdict into the new one. Neither answers "is this workload
        serving", so the workload is asked directly.

        Returns:
            ActiveStatus when Superset answers, MaintenanceStatus when it
            does not.
        """
        if self.config["charm-function"] not in UI_FUNCTIONS:
            return ActiveStatus("Status check: UP")

        if self._workload_is_serving():
            return ActiveStatus("Status check: UP")

        return MaintenanceStatus("Status check: DOWN")

    def _workload_is_serving(self):
        """Ask the workload whether it is serving.

        Returns:
            True if Superset answered its health endpoint.
        """
        try:
            return (
                requests.get(
                    HEALTH_URL, timeout=HEALTH_PROBE_TIMEOUT
                ).status_code
                == 200
            )
        except requests.exceptions.RequestException:
            return False

    def _database_has_data(self):
        """Return whether the PostgreSQL relation carries usable data."""
        return self.database.get_db_uri() is not None

    def _redis_has_data(self):
        """Return whether the Redis relation carries usable data."""
        return all(self.redis_handler.get_redis_relation_data())

    def _relation_status(self):
        """Report on the relations the workload cannot start without.

        A missing relation is the operator's to fix, so it blocks, and every
        one that is missing is named. A relation that exists but carries no
        data yet is the remote application still settling, which resolves
        on its own, so it waits.

        Returns:
            The status to report, or None when every required relation is
            usable.
        """
        required = (
            ("PostgreSQL", DB_RELATION_NAME, self._database_has_data),
            ("Redis", REDIS_RELATION_NAME, self._redis_has_data),
        )

        missing = [
            name
            for name, endpoint, _ in required
            if self.model.get_relation(endpoint) is None
        ]
        if missing:
            return BlockedStatus(
                f"Required relations missing: {', '.join(missing)}"
            )

        unready = [name for name, _, has_data in required if not has_data()]
        if unready:
            return WaitingStatus(
                f"Waiting for relation data: {', '.join(unready)}"
            )

        return None

    def _not_ready_status(self):
        """Report on whatever keeps the application from being started.

        Returns:
            The status to report, or None when the application can start.
        """
        config_status = self._config_status()
        if config_status is not None:
            return config_status

        try:
            self._signing_keys()
        except ValueError as e:
            return BlockedStatus(str(e))

        relation_status = self._relation_status()
        if relation_status is not None:
            return relation_status

        if self.admin_password() is None:
            # Only reachable on a follower before the leader has created the
            # secret, or before the peer relation exists.
            return WaitingStatus("waiting for the admin password secret")

        if self.oauth.is_related() and self.https_ingress_url is None:
            return BlockedStatus("OAuth requires an HTTPS ingress URL")

        return None

    def report_failure(self, status):
        """Record a failure for the status collector to report.

        The reconcile and the relation handlers do work whose outcome the
        model does not record afterwards, so the collector has no way
        to derive it. Those verdicts are handed over here instead.

        Args:
            status: The status to report for this dispatch.
        """
        self._reconcile_failure = status

    def _on_collect_unit_status(self, event: ops.CollectStatusEvent):
        """Report the unit status, the only place that decides it.

        Everything here is derived from what the model and the workload say,
        so the verdict does not depend on which event brought the charm to
        this point or on which handler ran last. The exception is
        `_reconcile_failure`: the outcome of work the reconcile attempted in
        this dispatch, which nothing in the model records afterwards.

        Args:
            event: The collect-unit-status event.
        """
        if self._reconcile_failure is not None:
            event.add_status(self._reconcile_failure)

        not_ready_status = self._not_ready_status()
        if not_ready_status is not None:
            event.add_status(not_ready_status)
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            event.add_status(
                WaitingStatus(f"waiting for {APP_NAME} container")
            )
            return

        event.add_status(self._workload_status())

    def _on_get_admin_password(self, event):
        """Return the generated admin password, action handler.

        Args:
            event: The event triggered by the get-admin-password action.
        """
        password = self.admin_password()
        if password is None:
            event.fail("the admin password has not been generated yet")
            return

        event.set_results({"password": password})

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
        sqlalchemy_uri = self.database.get_db_uri()
        if sqlalchemy_uri is None:
            raise ValueError("database relation data is not available")

        self._validate_self_registration_role(sqlalchemy_uri)

        (
            redis_hostname,
            redis_port,
        ) = self.redis_handler.get_redis_relation_data()
        if redis_hostname is None or redis_port is None:
            raise ValueError("redis relation data is not available")

        signing_keys = self._signing_keys()

        env = {
            "ALLOW_IMAGE_DOMAINS": self.config["allow-image-domains"],
            "SUPERSET_SECRET_KEY": signing_keys["secret-key"],
            "ADMIN_PASSWORD": self.admin_password(),
            "CHARM_FUNCTION": self.config["charm-function"].value,
            "SQL_ALCHEMY_URI": sqlalchemy_uri,
            "REDIS_HOST": redis_hostname,
            "REDIS_PORT": redis_port,
            "SQLALCHEMY_POOL_SIZE": self.config["sqlalchemy-pool-size"],
            "SQLALCHEMY_POOL_TIMEOUT": self.config["sqlalchemy-pool-timeout"],
            "SQLALCHEMY_MAX_OVERFLOW": self.config["sqlalchemy-max-overflow"],
            "OAUTH_ADMIN_EMAIL": self.config["oauth-admin-email"],
            "SELF_REGISTRATION_ROLE": self.config["self-registration-role"],
            "SUPERSET_LOAD_EXAMPLES": self.config["load-examples"],
            "PYTHONPATH": CONFIG_PATH,
            "HTML_SANITIZATION": self.config["html-sanitization"],
            "HTML_SANITIZATION_SCHEMA_EXTENSIONS": self.config[
                "html-sanitization-schema-extensions"
            ],
            "GLOBAL_ASYNC_QUERIES_JWT": signing_keys["async-queries-jwt"],
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
            # Explicitly set SUPERSET_PORT so the charm-supplied value always
            # overrides the service-discovery variable Kubernetes injects for a
            # service named "superset" (e.g. SUPERSET_PORT=tcp://10.x.x.x:65535),
            # which would otherwise break gunicorn's --bind. See issue #108.
            "SUPERSET_PORT": APPLICATION_PORT,
            "WEBSERVER_TIMEOUT": self.config["webserver-timeout"],
            "SCREENSHOT_TIMEOUT": self.config["screenshot-timeout"],
            "ALERT_REPORTS_DRY_RUN": self.config["report-dry-run"],
            "SERVER_WORKER_AMOUNT": self.config["server-worker-amount"],
            "GUNICORN_TIMEOUT": self.config["gunicorn-timeout"],
            "CELERY_WORKER_CONCURRENCY": self.config[
                "celery-worker-concurrency"
            ],
            "STATSD_PORT": STATSD_PORT,
            "LOG_FILE": LOG_FILE,
            "CACHE_WARMUP": self.config["cache-warmup"],
            "REDIS_TIMEOUT": self.config["redis-timeout"],
            "LOG_RETENTION_ENABLED": self.config["log-retention-enabled"],
            "LOG_RETENTION_DAYS": self.config["log-retention-days"],
            "DASHBOARD_SIZE_LIMIT": self.config["dashboard-size-limit"],
            "MAX_CONTENT_LENGTH": self.config["max-content-length"],
            "MAX_FORM_MEMORY_SIZE": self.config["max-form-memory-size"],
            "MAX_FORM_PARTS": self.config["max-form-parts"],
            "DATA_ACCESS_REQUEST_URL": self.config["data-access-request-url"],
            "ENABLE_RAISE_FOR_ACCESS_PATCH": self.config[
                "enable-raise-for-access-patch"
            ],
        }
        if self.config["feature-flags"]:
            env.update(self.config["feature-flags"])
        env.update(self._get_oauth_config())
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

    def _get_oauth_config(self):
        """Return OAuth provider information as workload environment values.

        Returns:
            The OAuth environment values, empty when OAuth is not configured.
        """
        provider = self.oauth.provider_info()
        if provider is None:
            return {}

        return {
            "OAUTH_ISSUER_URL": provider.issuer_url,
            "OAUTH_AUTHORIZATION_ENDPOINT": provider.authorization_endpoint,
            "OAUTH_TOKEN_ENDPOINT": provider.token_endpoint,
            "OAUTH_USERINFO_ENDPOINT": provider.userinfo_endpoint,
            "OAUTH_JWKS_ENDPOINT": provider.jwks_endpoint,
            "OAUTH_SCOPE": provider.scope,
            "OAUTH_CLIENT_ID": provider.client_id,
            "OAUTH_CLIENT_SECRET": provider.client_secret,
        }

    def _open_workload_ports(self):
        """Open the ports a UI application serves on."""
        if self.config["charm-function"] not in UI_FUNCTIONS:
            return

        # Open port for cache warm-up.
        self.model.unit.open_port(port=APPLICATION_PORT, protocol="tcp")

        # Open ports for accepting and exposing metrics
        self.model.unit.open_port(port=PROMETHEUS_METRICS_PORT, protocol="tcp")
        self.model.unit.open_port(port=STATSD_PORT, protocol="udp")

    def _sync_trino_catalogs(self, force_update_credentials):
        """Synchronise Trino catalogs into Superset database connections.

        Args:
            force_update_credentials: Whether to update every existing
                connection unconditionally.
        """
        if not self.model.get_relation(TRINO_CATALOG_RELATION_NAME):
            return

        self.trino_catalog_handler.sync_databases(
            force_update_credentials=force_update_credentials
        )

    def _pebble_layer(self, env):
        """Build the pebble layer for the configured charm function.

        Args:
            env: the workload environment.

        Returns:
            The pebble layer as a dictionary.
        """
        (
            redis_hostname,
            redis_port,
        ) = self.redis_handler.get_redis_relation_data()

        metrics_exporter_command = (
            f"/usr/bin/celery-exporter --broker-url redis://{redis_hostname}:{redis_port}/4 --port {PROMETHEUS_METRICS_PORT}"
            if self.config["charm-function"] == "worker"
            else "/usr/bin/statsd_exporter"
        )

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
                            "threshold": 1,
                            "http": {"url": HEALTH_URL},
                        }
                    }
                },
            )

        return pebble_layer

    def reconcile(self, force_trino_credentials: bool = False):
        """Reconcile the charm to its desired state.

        Single entry point for every observer: it reads the current config and
        relation state, decides whether the charm is ready, and ensures the
        workload plan matches.

        Args:
            force_trino_credentials: Whether to update every Trino database
                connection unconditionally, used when the credentials secret
                has rotated.
        """
        try:
            self.oauth.publish_client_config()
        except ClientConfigError as exc:
            logger.error("Invalid OAuth client configuration: %s", exc)
            self.report_failure(
                BlockedStatus("invalid OAuth client configuration")
            )
            return

        self._ensure_admin_password()

        if self._not_ready_status() is not None:
            return

        container = self.unit.get_container(self.name)
        if not container.can_connect():
            return

        logger.info("configuring %s", APP_NAME)
        try:
            env = self._create_env()
        except ValueError as e:
            self.report_failure(BlockedStatus(str(e)))
            return

        if not self.reconcile_certificates():
            return

        load_superset_files(container)

        self._open_workload_ports()

        logger.info("planning %s execution", APP_NAME)
        try:
            container.add_layer(
                self.name, self._pebble_layer(env), combine=True
            )
            container.replan()
        except (pebble.ChangeError, pebble.ConnectionError) as e:
            # A pod being torn down fails the replan rather than the charm:
            # `can_connect` goes stale, and pebble goes away with the pod.
            logger.warning("Pebble replan failed: %s", e)
            self.report_failure(MaintenanceStatus("replan failed"))
            return

        self._sync_trino_catalogs(force_trino_credentials)

        self.unit.set_workload_version(f"v{SUPERSET_VERSION}")


if __name__ == "__main__":
    ops.main(SupersetK8SCharm)
