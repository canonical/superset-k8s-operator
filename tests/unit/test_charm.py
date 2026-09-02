# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


"""Charm unit tests."""

# pylint:disable=protected-access

import dataclasses
import json
import logging
from unittest import mock

from ops import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import CheckStatus, Layer
from ops.testing import CheckInfo, Secret, State

from literals import CA_CERT_LOCAL_PATH, CA_CERT_PATH
from tests.unit.helpers import (
    CA_PEM,
    INCOMPLETE_PEBBLE_PLAN,
    MODEL_NAME,
    SECRET_KEY,
    SERVER_PORT,
    SMTP_SECRET_CONTENTS,
    build_state,
    google_oauth_relation,
    ingress_relation,
    mcp_environment,
    mcp_ingress_relation,
    oauth_relation,
    oauth_secret,
    superset_container,
    superset_environment,
)

logger = logging.getLogger(__name__)

WANT_ENVIRONMENT = {
    "ALLOW_IMAGE_DOMAINS": None,
    "SUPERSET_SECRET_KEY": SECRET_KEY,
    "ADMIN_PASSWORD": "admin",  # nosec B105
    "CHARM_FUNCTION": "app-gunicorn",
    "SQL_ALCHEMY_URI": "postgresql://postgres_user:admin@myhost:5432/superset",
    "REDIS_HOST": "redis-host",
    "REDIS_PORT": 6379,
    "REDIS_TIMEOUT": 300,
    "SQLALCHEMY_POOL_SIZE": 5,
    "SQLALCHEMY_POOL_TIMEOUT": 300,
    "SQLALCHEMY_MAX_OVERFLOW": 5,
    "OAUTH_ADMIN_EMAIL": "admin@superset.com",
    "SELF_REGISTRATION_ROLE": "Public",
    "SUPERSET_LOAD_EXAMPLES": False,
    "PYTHONPATH": "/app/pythonpath",
    "HTML_SANITIZATION": True,
    "HTML_SANITIZATION_SCHEMA_EXTENSIONS": None,
    "GLOBAL_ASYNC_QUERIES_JWT": (
        "18b2f8fcd0d708d270c00508da6e8dfc7a21eff14ea438056809805150439a04"
    ),
    "GLOBAL_ASYNC_QUERIES_POLLING_DELAY": 500,
    "SENTRY_DSN": None,
    "SENTRY_ENVIRONMENT": None,
    "SENTRY_RELEASE": None,
    "SENTRY_REDACT_PARAMS": False,
    "SENTRY_SAMPLE_RATE": 1.0,
    "SERVER_ALIAS": "superset-k8s",
    "APPLICATION_PORT": 8088,
    "SUPERSET_PORT": 8088,
    "WEBSERVER_TIMEOUT": 180,
    "SCREENSHOT_TIMEOUT": 600,
    "ALERT_REPORTS_DRY_RUN": False,
    "SERVER_WORKER_AMOUNT": 1,
    "GUNICORN_TIMEOUT": 60,
    "CELERY_WORKER_CONCURRENCY": 0,
    "STATSD_PORT": 9125,
    "LOG_FILE": "/var/log/superset.log",
    "CACHE_WARMUP": False,
    "LOG_RETENTION_ENABLED": True,
    "LOG_RETENTION_DAYS": 730,
    "DASHBOARD_SIZE_LIMIT": 65535,
    "MAX_CONTENT_LENGTH": None,
    "MAX_FORM_MEMORY_SIZE": None,
    "MAX_FORM_PARTS": None,
    "DATA_ACCESS_REQUEST_URL": None,
    "ENABLE_RAISE_FOR_ACCESS_PATCH": False,
}


def test_initial_plan(ctx):
    """The initial pebble plan is empty."""
    state = State(leader=True, containers={superset_container()})

    with ctx(ctx.on.update_status(), state) as manager:
        container = manager.charm.unit.get_container("superset")
        assert container.get_plan().to_dict() == {}


def test_ready(ctx):
    """The pebble plan is correctly generated when the charm is ready."""
    container = superset_container()
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

    plan = state_out.get_container("superset").plan.to_dict()
    assert plan["services"]["superset"] == {
        "override": "replace",
        "summary": "superset server",
        "command": "/app/k8s/k8s-bootstrap.sh",
        "startup": "enabled",
        "environment": WANT_ENVIRONMENT,
        "on-check-failure": {"up": "ignore"},
    }

    # The service was started.
    assert (
        state_out.get_container("superset").services["superset"].is_running()
    )

    # The MaintenanceStatus is set with replan message.
    assert state_out.unit_status == MaintenanceStatus("replanning application")


def test_config_changed(ctx):
    """The pebble plan changes according to config changes."""
    state_in = build_state(
        config={
            "admin-password": "secure-pass",
            "allow-image-domains": "assets.ubuntu.com",
            "feature-flags": "ALLOW_ADHOC_SUBQUERY, !GLOBAL_ASYNC_QUERIES",
            "enable-raise-for-access-patch": True,
        }
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    want_environment = dict(WANT_ENVIRONMENT)
    want_environment.update(
        {
            "ALLOW_IMAGE_DOMAINS": "assets.ubuntu.com",
            "ADMIN_PASSWORD": "secure-pass",  # nosec B105
            "ALLOW_ADHOC_SUBQUERY": True,
            "GLOBAL_ASYNC_QUERIES": False,
            "ENABLE_RAISE_FOR_ACCESS_PATCH": True,
        }
    )
    assert superset_environment(state_out) == want_environment
    assert state_out.unit_status == MaintenanceStatus("replanning application")


def test_observability_pebble_layer(ctx):
    """The metrics exporter service is part of the generated plan."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    plan = state_out.get_container("superset").plan.to_dict()
    assert plan["services"]["metrics-exporter"] == {
        "override": "replace",
        "summary": "metrics exporter",
        "command": "/usr/bin/statsd_exporter",
        "startup": "enabled",
        "after": ["superset"],
    }


def test_ingress_requirer_publishes_databag(ctx):
    """The charm advertises its workload port to the ingress provider."""
    ingress = ingress_relation(url=None)
    state_in = build_state(extra_relations=(ingress,))

    state_out = ctx.run(ctx.on.relation_changed(ingress), state_in)

    databag = state_out.get_relation(ingress.id).local_app_data
    assert databag["port"] == SERVER_PORT
    assert databag["strip-prefix"] == "true"
    assert databag["redirect-https"] == "true"
    assert json.loads(databag["model"]) == MODEL_NAME
    assert json.loads(databag["name"]) == "superset-k8s"


def test_ingress_url_is_available_to_the_charm(ctx):
    """A URL published by the provider is readable as the external URL."""
    state_in = build_state(
        extra_relations=(ingress_relation("https://superset.example"),)
    )

    with ctx(ctx.on.config_changed(), state_in) as manager:
        manager.run()
        assert manager.charm.https_ingress_url == "https://superset.example"


def test_ingress_url_without_tls_is_not_used(ctx):
    """A plain HTTP URL is not treated as an external HTTPS URL."""
    state_in = build_state(
        extra_relations=(ingress_relation("http://superset.example"),)
    )

    with ctx(ctx.on.config_changed(), state_in) as manager:
        manager.run()
        assert manager.charm.https_ingress_url is None


def test_ingress_ready_republishes_oauth_client_config(ctx):
    """A URL arriving from the provider reaches the identity provider."""
    oauth = oauth_relation()
    ingress = ingress_relation("https://superset.example")
    state_in = build_state(extra_relations=(oauth, ingress))

    state_out = ctx.run(ctx.on.relation_changed(ingress), state_in)

    assert state_out.get_relation(oauth.id).local_app_data["redirect_uri"] == (
        "https://superset.example/oauth-authorized/oidc"
    )


def test_oauth_leader_publishes_client_config(ctx):
    """The leader registers Superset's OIDC callback with the provider."""
    oauth = oauth_relation()
    state_in = build_state(
        extra_relations=(oauth, ingress_relation("https://superset.example"))
    )

    state_out = ctx.run(ctx.on.relation_created(oauth), state_in)

    databag = state_out.get_relation(oauth.id).local_app_data
    assert databag["redirect_uri"] == (
        "https://superset.example/oauth-authorized/oidc"
    )
    assert databag["scope"] == "openid email profile"
    assert json.loads(databag["grant_types"]) == ["authorization_code"]


def test_oauth_redirect_uri_strips_trailing_slash(ctx):
    """A root-serving provider URL does not yield a double-slash callback."""
    oauth = oauth_relation()
    state_in = build_state(
        extra_relations=(oauth, ingress_relation("https://superset.example/"))
    )

    state_out = ctx.run(ctx.on.relation_created(oauth), state_in)

    assert state_out.get_relation(oauth.id).local_app_data["redirect_uri"] == (
        "https://superset.example/oauth-authorized/oidc"
    )


def test_oauth_without_ingress_does_not_publish_client_config(ctx):
    """Registration waits for the provider to publish an external URL."""
    oauth = oauth_relation()
    state_in = build_state(extra_relations=(oauth,))

    state_out = ctx.run(ctx.on.relation_created(oauth), state_in)

    assert (
        "redirect_uri" not in state_out.get_relation(oauth.id).local_app_data
    )


def test_oauth_without_ingress_blocks_the_unit(ctx):
    """An OAuth relation without an HTTPS ingress URL blocks the unit."""
    state_in = build_state(extra_relations=(oauth_relation(),))

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "OAuth requires an HTTPS ingress URL"
    )


def test_oauth_non_leader_does_not_publish_client_config(ctx):
    """Only the leader writes OAuth client registration data."""
    oauth = oauth_relation()
    state_in = build_state(
        leader=False,
        extra_relations=(oauth, ingress_relation("https://superset.example")),
    )

    state_out = ctx.run(ctx.on.relation_created(oauth), state_in)

    assert state_out.get_relation(oauth.id).local_app_data == {}


def test_oauth_provider_populates_environment(ctx):
    """Provider relation data and its secret configure Superset OIDC."""
    secret = oauth_secret()
    state_in = build_state(
        extra_relations=(
            oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["OAUTH_ISSUER_URL"] == "https://idp.example"
    assert environment["OAUTH_AUTHORIZATION_ENDPOINT"] == (
        "https://idp.example/authorize"
    )
    assert environment["OAUTH_TOKEN_ENDPOINT"] == "https://idp.example/token"
    assert environment["OAUTH_USERINFO_ENDPOINT"] == (
        "https://idp.example/userinfo"
    )
    assert environment["OAUTH_JWKS_ENDPOINT"] == "https://idp.example/jwks"
    assert environment["OAUTH_SCOPE"] == "openid email profile"
    assert environment["OAUTH_CLIENT_ID"] == "superset-client"
    assert environment["OAUTH_CLIENT_SECRET"] == "secret-value"


def test_oauth_incomplete_registration_is_not_enabled(ctx):
    """A relation without provider credentials leaves OAuth disabled."""
    state_in = build_state(
        extra_relations=(
            oauth_relation(),
            ingress_relation("https://superset.example"),
        )
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert not [
        key
        for key in superset_environment(state_out)
        if key.startswith("OAUTH_") and key != "OAUTH_ADMIN_EMAIL"
    ]


def test_oauth_inaccessible_secret_is_not_enabled(ctx):
    """Wait safely while OAuth provider credentials are inaccessible."""
    state_in = build_state(
        extra_relations=(
            oauth_relation("secret:not-granted-to-superset"),
            ingress_relation("https://superset.example"),
        )
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert "OAUTH_CLIENT_ID" not in superset_environment(state_out)


def test_oauth_relation_removal_clears_environment(ctx):
    """Removing the provider relation removes workload OAuth settings."""
    secret = oauth_secret()
    oauth = oauth_relation(secret.id)
    state_in = build_state(
        extra_relations=(
            oauth,
            ingress_relation("https://superset.example"),
        ),
        secrets=(secret,),
    )

    state_mid = ctx.run(ctx.on.config_changed(), state_in)
    assert "OAUTH_CLIENT_ID" in superset_environment(state_mid)

    state_broken = ctx.run(
        ctx.on.relation_broken(state_mid.get_relation(oauth.id)), state_mid
    )

    assert "OAUTH_CLIENT_ID" not in superset_environment(state_broken)


def test_oauth_secret_change_refreshes_credentials(ctx):
    """A provider credential update is reflected in the workload."""
    secret = oauth_secret(latest_value="rotated-secret")
    state_in = build_state(
        extra_relations=(
            oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.secret_changed(secret), state_in)

    assert superset_environment(state_out)["OAUTH_CLIENT_SECRET"] == (
        "rotated-secret"
    )


def with_check(state, status):
    """Return the state with a pebble check reported on the container.

    The check can only be declared once the plan the charm applied carries
    it, so this is applied to the state a first reconcile produced.

    Args:
        state: a state whose plan already declares the `up` check.
        status: the status the check reports.

    Returns:
        A new `State` whose container reports the check.
    """
    container = dataclasses.replace(
        state.get_container("superset"),
        check_infos={CheckInfo("up", status=status)},
    )
    return dataclasses.replace(state, containers={container})


def test_update_status_up(ctx):
    """The charm updates the unit status to active based on UP status."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.update_status(), with_check(state_mid, CheckStatus.UP)
    )

    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_update_status_down(ctx):
    """The charm reports maintenance when the pebble check is DOWN."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    state_out = ctx.run(
        ctx.on.update_status(), with_check(state_mid, CheckStatus.DOWN)
    )

    assert state_out.unit_status == MaintenanceStatus("Status check: DOWN")


def test_incomplete_pebble_plan(ctx):
    """The charm re-applies the pebble plan if incomplete."""
    container = dataclasses.replace(
        superset_container(),
        layers={"superset": Layer(INCOMPLETE_PEBBLE_PLAN)},
    )
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.update_status(), state_in)

    assert state_out.unit_status == MaintenanceStatus("replanning application")
    assert (
        state_out.get_container("superset").plan.to_dict()
        != INCOMPLETE_PEBBLE_PLAN
    )


def test_missing_pebble_plan(ctx):
    """The charm re-applies the pebble plan if missing."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())

    with mock.patch(
        "charm.SupersetK8SCharm._validate_pebble_plan", return_value=False
    ):
        state_out = ctx.run(ctx.on.update_status(), state_mid)

    assert state_out.unit_status == MaintenanceStatus("replanning application")
    assert state_out.get_container("superset").plan.to_dict() is not None


def test_secret_key_uses_configured_value(ctx):
    """SUPERSET_SECRET_KEY uses the value of superset-secret-key."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    assert superset_environment(state_out)["SUPERSET_SECRET_KEY"] == SECRET_KEY


def test_secret_key_blocked_when_not_configured(ctx):
    """The charm sets BlockedStatus when superset-secret-key is missing."""
    state_in = build_state()
    state_in = dataclasses.replace(state_in, config={})

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert state_out.unit_status == BlockedStatus(
        "missing required config: superset-secret-key"
    )

    state_out = ctx.run(ctx.on.update_status(), state_out)
    assert state_out.unit_status == BlockedStatus(
        "missing required config: superset-secret-key"
    )


def test_beat_deployment(ctx):
    """The pebble plan reflects the beat function."""
    state_in = build_state(config={"charm-function": "beat"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert superset_environment(state_out)["CHARM_FUNCTION"] == "beat"
    assert state_out.unit_status == MaintenanceStatus("replanning application")


def test_worker_deployment(ctx):
    """The pebble plan reflects the worker function."""
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert superset_environment(state_out)["CHARM_FUNCTION"] == "worker"
    assert state_out.unit_status == MaintenanceStatus("replanning application")


def test_invalid_default_role(ctx):
    """An unknown self-registration role blocks the unit."""
    state_in = build_state(config={"self-registration-role": "InvalidRole"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "The self-registration role InvalidRole is not allowed. "
        "Use only ['Public', 'Gamma', 'Alpha', 'Admin']."
    )


def test_smtp_handling_without_secret(ctx):
    """No SMTP variables are rendered when no SMTP secret is configured."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    environment = superset_environment(state_out)
    assert not [key for key in environment if key.startswith("SMTP_")]


def test_smtp_handling_with_secret(ctx):
    """A granted SMTP secret is rendered into the workload environment."""
    secret = Secret(tracked_content=SMTP_SECRET_CONTENTS, owner=None)
    state_in = build_state(
        config={"smtp-secret-id": secret.id}, secrets=(secret,)
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_HOST"] == SMTP_SECRET_CONTENTS["host"]
    assert environment["SMTP_PORT"] == SMTP_SECRET_CONTENTS["port"]
    assert environment["SMTP_USERNAME"] == SMTP_SECRET_CONTENTS["username"]
    assert environment["SMTP_PASSWORD"] == SMTP_SECRET_CONTENTS["password"]
    assert environment["SMTP_EMAIL"] == SMTP_SECRET_CONTENTS["email"]
    assert environment["SMTP_SSL"] == SMTP_SECRET_CONTENTS["ssl"]
    assert environment["SMTP_STARTTLS"] == SMTP_SECRET_CONTENTS["starttls"]
    assert environment["SMTP_SSL_SERVER_AUTH"] == (
        SMTP_SECRET_CONTENTS["ssl-server-auth"]
    )
    assert environment["SMTP_SUPERSET_EXTERNAL_URL"] == (
        SMTP_SECRET_CONTENTS["superset-external-url"]
    )
    assert environment["SMTP_EMAIL_SUBJECT_PREFIX"] == (
        SMTP_SECRET_CONTENTS["email-subject-prefix"]
    )


def test_smtp_handling_with_improper_secret(ctx):
    """An SMTP secret missing a required key blocks the unit."""
    contents = {
        key: value
        for key, value in SMTP_SECRET_CONTENTS.items()
        if key != "host"
    }
    secret = Secret(tracked_content=contents, owner=None)
    state_in = build_state(
        config={"smtp-secret-id": secret.id}, secrets=(secret,)
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        f"SMTP secret with ID '{secret.id}' has improper schema. "
        "Missing: host"
    )


def test_smtp_handling_with_missing_secret(ctx):
    """An SMTP secret ID that does not resolve blocks the unit."""
    state_in = build_state(config={"smtp-secret-id": "i-dont-exist"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "SMTP secret with ID 'i-dont-exist' cannot be found."
    )


def test_certificates_reconcile_installs_ca(ctx):
    """The CA is installed into the container when a cert is assigned."""
    with ctx(ctx.on.config_changed(), build_state()) as manager:
        with mock.patch.object(
            manager.charm.certificates_handler,
            "_assigned_ca",
            return_value=CA_PEM,
        ):
            assert manager.charm.reconcile_certificates()

        container = manager.charm.unit.get_container("superset")
        assert container.pull(CA_CERT_PATH).read() == CA_PEM
        assert container.pull(CA_CERT_LOCAL_PATH).read() == CA_PEM
        assert [args.command for args in ctx.exec_history["superset"]] == [
            ["update-ca-certificates"]
        ]


def test_certificates_reconcile_is_idempotent(ctx):
    """An already installed CA is not re-installed on every reconcile."""
    with ctx(ctx.on.config_changed(), build_state()) as manager:
        with mock.patch.object(
            manager.charm.certificates_handler,
            "_assigned_ca",
            return_value=CA_PEM,
        ):
            manager.charm.reconcile_certificates()
            manager.charm.reconcile_certificates()

        assert len(ctx.exec_history["superset"]) == 1


def test_certificates_reconcile_reinstalls_wiped_ca(ctx):
    """The CA is re-installed after a pod respawn wipes the filesystem."""
    with ctx(ctx.on.config_changed(), build_state()) as manager:
        container = manager.charm.unit.get_container("superset")
        with mock.patch.object(
            manager.charm.certificates_handler,
            "_assigned_ca",
            return_value=CA_PEM,
        ):
            manager.charm.reconcile_certificates()
            container.remove_path(CA_CERT_PATH)
            container.remove_path(CA_CERT_LOCAL_PATH)
            manager.charm.reconcile_certificates()

        assert container.pull(CA_CERT_PATH).read() == CA_PEM


def test_certificates_trust_store_failure_blocks_unit(ctx):
    """A failing trust store update blocks the unit instead of passing."""
    container = superset_container(exec_return_code=1)
    state_in = build_state(container=container)

    with ctx(ctx.on.config_changed(), state_in) as manager:
        with mock.patch.object(
            manager.charm.certificates_handler,
            "_assigned_ca",
            return_value=CA_PEM,
        ):
            assert not manager.charm.reconcile_certificates()

        assert isinstance(manager.charm.unit.status, BlockedStatus)


def test_certificates_relation_broken_removes_ca(ctx):
    """The CA is removed from the container when the relation breaks."""
    with ctx(ctx.on.config_changed(), build_state()) as manager:
        container = manager.charm.unit.get_container("superset")
        container.push(CA_CERT_PATH, CA_PEM, make_dirs=True)
        container.push(CA_CERT_LOCAL_PATH, CA_PEM, make_dirs=True)

        manager.charm.reconcile_certificates(relation_broken=True)

        assert not container.exists(CA_CERT_PATH)
        assert not container.exists(CA_CERT_LOCAL_PATH)


def _mcp_config(**overrides):
    """Return charm config enabling authenticated MCP.

    Args:
        overrides: Config values overriding the defaults.

    Returns:
        A config dict suitable for build_state().
    """
    config = {
        "mcp-enabled": True,
        "mcp-auth-enabled": True,
    }
    config.update(overrides)
    return config


def test_google_oauth_does_not_require_mcp_ingress(ctx):
    """A Google-backed oauth relation reconciles cleanly without mcp-ingress.

    mcp-ingress is not required to start MCP -- MCP_AUTH_BASE_URL is simply
    empty until it's related. Left as a deployer's responsibility to wire
    up before Google login can actually complete end-to-end.
    """
    secret = oauth_secret()
    state_in = build_state(
        config=_mcp_config(),
        extra_relations=(
            google_oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == MaintenanceStatus("replanning application")
    assert mcp_environment(state_out)["MCP_AUTH_BASE_URL"] == ""


def test_google_oauth_ready_with_mcp_ingress(ctx):
    """A Google-backed oauth relation with mcp-ingress reconciles cleanly."""
    secret = oauth_secret()
    state_in = build_state(
        config=_mcp_config(),
        extra_relations=(
            google_oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
            mcp_ingress_relation(),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == MaintenanceStatus("replanning application")


def test_google_oauth_env_built_from_relation(ctx):
    """MCP env is sourced from the same oauth relation used for the web UI."""
    secret = oauth_secret()
    state_in = build_state(
        config=_mcp_config(),
        extra_relations=(
            google_oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
            mcp_ingress_relation("traefik.example"),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = mcp_environment(state_out)
    assert environment["MCP_AUTH_ISSUER"] == "https://accounts.google.com"
    assert environment["MCP_AUTH_INTROSPECTION_URL"] == (
        "https://oauth2.googleapis.com/tokeninfo"
    )
    assert environment["MCP_AUTH_CLIENT_ID"] == "google-client-id"
    assert environment["MCP_AUTH_CLIENT_SECRET"] == "secret-value"
    # Derived from Traefik's root external_host, not a literal URL the
    # provider publishes -- see SupersetK8SCharm._mcp_route_host.
    assert environment["MCP_AUTH_BASE_URL"] == (
        f"https://{MODEL_NAME}-superset-k8s-mcp.traefik.example"
    )
    assert environment["MCP_AUTH_CLIENT_REGISTRATION"] == "true"


def test_google_oauth_client_registration_disabled(ctx):
    """mcp-auth-client-registration=false is rendered lowercase into the env."""
    secret = oauth_secret()
    state_in = build_state(
        config=_mcp_config(**{"mcp-auth-client-registration": False}),
        extra_relations=(
            google_oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
            mcp_ingress_relation(),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert (
        mcp_environment(state_out)["MCP_AUTH_CLIENT_REGISTRATION"] == "false"
    )


def test_hydra_oauth_does_not_require_mcp_ingress(ctx):
    """A standard (non-Google) oauth relation never needed mcp-ingress."""
    secret = oauth_secret()
    state_in = build_state(
        config=_mcp_config(),
        extra_relations=(
            oauth_relation(secret.id),
            ingress_relation("https://superset.example"),
        ),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == MaintenanceStatus("replanning application")
    environment = mcp_environment(state_out)
    assert environment["MCP_AUTH_CLIENT_ID"] == "superset-client"
    assert environment["MCP_AUTH_BASE_URL"] == ""
