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

from ops import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ChangeError
from ops.pebble import ConnectionError as PebbleConnectionError
from ops.pebble import Layer
from ops.testing import Relation, Secret, State

from literals import CA_CERT_LOCAL_PATH, CA_CERT_PATH
from relations.tls import Certificates
from tests.unit.helpers import (
    ASYNC_QUERIES_JWT,
    CA_PEM,
    INCOMPLETE_PEBBLE_PLAN,
    MODEL_NAME,
    SECRET_KEY,
    SERVER_PORT,
    SMTP_SECRET_CONTENTS,
    TRINO_CREDENTIALS,
    TRINO_CREDENTIALS_NEW,
    build_state,
    ingress_relation,
    oauth_relation,
    oauth_secret,
    signing_keys_secret,
    superset_container,
    superset_environment,
    trino_catalog_relation,
)

logger = logging.getLogger(__name__)

WANT_ENVIRONMENT = {
    "ALLOW_IMAGE_DOMAINS": None,
    "SUPERSET_SECRET_KEY": SECRET_KEY,
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
    "GLOBAL_ASYNC_QUERIES_JWT": ASYNC_QUERIES_JWT,
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
    environment = plan["services"]["superset"]["environment"]
    assert environment["ADMIN_PASSWORD"]
    assert plan["services"]["superset"] == {
        "override": "replace",
        "summary": "superset server",
        "command": "/app/k8s/k8s-bootstrap.sh",
        "startup": "enabled",
        "environment": WANT_ENVIRONMENT
        | {"ADMIN_PASSWORD": environment["ADMIN_PASSWORD"]},
        "on-check-failure": {"up": "ignore"},
    }

    # The service was started.
    assert (
        state_out.get_container("superset").services["superset"].is_running()
    )

    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_config_changed(ctx):
    """The pebble plan changes according to config changes."""
    state_in = build_state(
        config={
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
            "ALLOW_ADHOC_SUBQUERY": True,
            "GLOBAL_ASYNC_QUERIES": False,
            "ENABLE_RAISE_FOR_ACCESS_PATCH": True,
        }
    )
    environment = superset_environment(state_out)
    assert environment["ADMIN_PASSWORD"]
    want_environment["ADMIN_PASSWORD"] = environment["ADMIN_PASSWORD"]
    assert environment == want_environment
    assert state_out.unit_status == ActiveStatus("Status check: UP")


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


def test_incomplete_pebble_plan(ctx):
    """The charm re-applies the pebble plan if incomplete."""
    container = dataclasses.replace(
        superset_container(),
        layers={"superset": Layer(INCOMPLETE_PEBBLE_PLAN)},
    )
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.update_status(), state_in)

    assert state_out.unit_status == ActiveStatus("Status check: UP")
    assert (
        state_out.get_container("superset").plan.to_dict()
        != INCOMPLETE_PEBBLE_PLAN
    )


def test_missing_pebble_plan(ctx):
    """The charm re-applies the pebble plan if missing.

    A rescheduled pod comes back with an empty plan, and `update-status` is
    what notices when no other event has.
    """
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    wiped = dataclasses.replace(
        state_mid.get_container("superset"),
        layers={},
        service_statuses={},
        check_infos=frozenset(),
    )
    state_in = dataclasses.replace(state_mid, containers={wiped})

    state_out = ctx.run(ctx.on.update_status(), state_in)

    plan = state_out.get_container("superset").plan.to_dict()
    assert plan["services"]["superset"]["command"]
    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_signing_keys_reach_the_workload(ctx):
    """Both signing keys are read out of the user secret."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    environment = superset_environment(state_out)
    assert environment["SUPERSET_SECRET_KEY"] == SECRET_KEY
    assert environment["GLOBAL_ASYNC_QUERIES_JWT"] == ASYNC_QUERIES_JWT


def test_signing_keys_blocked_when_not_configured(ctx):
    """The charm blocks when signing-keys-secret-id is unset."""
    state_in = build_state()
    state_in = dataclasses.replace(state_in, config={})

    state_out = ctx.run(ctx.on.config_changed(), state_in)
    assert state_out.unit_status == BlockedStatus(
        "missing required config: signing-keys-secret-id"
    )

    state_out = ctx.run(ctx.on.update_status(), state_out)
    assert state_out.unit_status == BlockedStatus(
        "missing required config: signing-keys-secret-id"
    )


def test_signing_keys_blocked_when_secret_is_missing(ctx):
    """A secret ID that does not resolve blocks the unit."""
    state_in = build_state()
    state_in = dataclasses.replace(
        state_in, config={"signing-keys-secret-id": "secret:does-not-exist"}
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "signing keys secret 'secret:does-not-exist' cannot be found."
    )


def test_signing_keys_blocked_when_a_key_is_missing(ctx):
    """A secret without both keys blocks the unit, naming the missing one."""
    secret = signing_keys_secret(content={"secret-key": SECRET_KEY})
    state_in = build_state(signing_keys=secret)

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        f"signing keys secret '{secret.id}' has improper schema. "
        "Missing: async-queries-jwt"
    )


def test_admin_password_is_generated_once(ctx):
    """The leader generates the password and publishes the secret ID."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    peer = [
        relation
        for relation in state_out.relations
        if relation.endpoint == "peer"
    ][0]
    secret_id = peer.local_app_data["admin-password-secret-id"]
    assert secret_id

    password = superset_environment(state_out)["ADMIN_PASSWORD"]
    assert password

    state_again = ctx.run(ctx.on.config_changed(), state_out)
    assert superset_environment(state_again)["ADMIN_PASSWORD"] == password


def test_admin_password_action_returns_the_password(ctx):
    """The action returns the same password the workload was given."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())
    password = superset_environment(state_out)["ADMIN_PASSWORD"]

    ctx.run(ctx.on.action("get-admin-password"), state_out)

    assert ctx.action_results == {"password": password}


def test_follower_waits_for_the_leader_to_generate_the_password(ctx):
    """A follower does not generate its own password."""
    state_out = ctx.run(ctx.on.config_changed(), build_state(leader=False))

    assert state_out.unit_status == WaitingStatus(
        "waiting for the admin password secret"
    )


def test_follower_reads_the_password_the_leader_generated(ctx):
    """A follower resolves the secret by the ID on the peer databag."""
    state_leader = ctx.run(ctx.on.config_changed(), build_state())
    password = superset_environment(state_leader)["ADMIN_PASSWORD"]

    state_follower = dataclasses.replace(state_leader, leader=False)
    state_out = ctx.run(ctx.on.config_changed(), state_follower)

    assert superset_environment(state_out)["ADMIN_PASSWORD"] == password


def test_beat_deployment(ctx):
    """The pebble plan reflects the beat function."""
    state_in = build_state(config={"charm-function": "beat"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert superset_environment(state_out)["CHARM_FUNCTION"] == "beat"
    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_worker_deployment(ctx):
    """The pebble plan reflects the worker function."""
    state_in = build_state(config={"charm-function": "worker"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert superset_environment(state_out)["CHARM_FUNCTION"] == "worker"
    assert state_out.unit_status == ActiveStatus("Status check: UP")


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
    """A failing trust store update blocks the unit instead of passing.

    The failure is the outcome of work rather than something the model
    records, so it only reaches the operator if the reconcile hands it to
    the status collector.
    """
    container = superset_container(exec_return_code=1)
    state_in = build_state(container=container)

    with mock.patch.object(Certificates, "_assigned_ca", return_value=CA_PEM):
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert isinstance(state_out.unit_status, BlockedStatus)


def test_certificates_relation_broken_removes_ca(ctx):
    """The CA is removed from the container when the relation breaks."""
    with ctx(ctx.on.config_changed(), build_state()) as manager:
        container = manager.charm.unit.get_container("superset")
        container.push(CA_CERT_PATH, CA_PEM, make_dirs=True)
        container.push(CA_CERT_LOCAL_PATH, CA_PEM, make_dirs=True)

        manager.charm.reconcile_certificates(relation_broken=True)

        assert not container.exists(CA_CERT_PATH)
        assert not container.exists(CA_CERT_LOCAL_PATH)


def test_container_not_ready_waits(ctx):
    """The unit waits rather than planning when pebble is unreachable."""
    container = dataclasses.replace(superset_container(), can_connect=False)
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "waiting for superset container"
    )


def test_update_status_reports_unreachable_container(ctx):
    """An unreachable container is reported rather than replanned.

    A pod whose workload container has not come up yet resolves on its own,
    and reads the same on the periodic hook as on any other event.
    """
    container = dataclasses.replace(superset_container(), can_connect=False)
    state_in = build_state(container=container)

    state_out = ctx.run(ctx.on.update_status(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "waiting for superset container"
    )


def test_failed_replan_is_reported(ctx):
    """A replan that fails leaves the unit in maintenance, not active."""
    for failure in (
        ChangeError("error", mock.Mock(tasks=[])),
        PebbleConnectionError("pebble went away with the pod"),
    ):
        with mock.patch("ops.model.Container.replan", side_effect=failure):
            state_out = ctx.run(ctx.on.config_changed(), build_state())

        assert state_out.unit_status == MaintenanceStatus("replan failed")


def test_update_status_republishes_ingress_address(ctx):
    """The unit address is republished even while the charm is blocked.

    The address is the charm's own self-describing databag field, and a bad
    read taken during a pod reschedule is never revisited otherwise. It must
    not be gated on the charm being ready.
    """
    ingress = ingress_relation()
    state_in = build_state(extra_relations=(ingress,), with_database=False)

    state_out = ctx.run(ctx.on.update_status(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: PostgreSQL"
    )
    assert state_out.get_relation(ingress.id).local_unit_data["ip"]


def test_database_relation_broken_blocks_without_deferring(ctx):
    """Losing PostgreSQL blocks immediately and defers nothing."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    database = [
        relation
        for relation in state_mid.relations
        if relation.endpoint == "postgresql_db"
    ][0]

    state_out = ctx.run(ctx.on.relation_broken(database), state_mid)

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: PostgreSQL"
    )
    assert state_out.deferred == []


def test_redis_relation_broken_blocks_without_deferring(ctx):
    """Losing Redis blocks immediately and defers nothing."""
    state_mid = ctx.run(ctx.on.config_changed(), build_state())
    redis = [
        relation
        for relation in state_mid.relations
        if relation.endpoint == "redis"
    ][0]

    state_out = ctx.run(ctx.on.relation_broken(redis), state_mid)

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: Redis"
    )
    assert state_out.deferred == []


def test_oauth_relation_broken_does_not_defer(ctx):
    """Losing the OAuth provider reconciles immediately."""
    secret = oauth_secret()
    oauth = oauth_relation(secret.id)
    state_in = build_state(
        extra_relations=(oauth, ingress_relation("https://superset.example")),
        secrets=(secret,),
    )
    state_mid = ctx.run(ctx.on.config_changed(), state_in)

    state_out = ctx.run(
        ctx.on.relation_broken(state_mid.get_relation(oauth.id)), state_mid
    )

    assert state_out.unit_status == ActiveStatus("Status check: UP")
    assert state_out.deferred == []


def test_ingress_relation_broken_does_not_defer(ctx):
    """Losing the ingress provider reconciles immediately."""
    ingress = ingress_relation()
    state_mid = ctx.run(
        ctx.on.config_changed(), build_state(extra_relations=(ingress,))
    )

    state_out = ctx.run(
        ctx.on.relation_broken(state_mid.get_relation(ingress.id)), state_mid
    )

    assert state_out.deferred == []


def test_trino_catalog_relation_broken_keeps_databases(ctx):
    """A broken trino-catalog relation leaves Superset databases alone."""
    secret = Secret(
        tracked_content=TRINO_CREDENTIALS,
    )
    trino = trino_catalog_relation(secret.id)
    state_in = build_state(extra_relations=(trino,), secrets=(secret,))

    with mock.patch(
        "relations.trino_catalog.TrinoCatalogRelationHandler.sync_databases"
    ) as sync:
        state_out = ctx.run(ctx.on.relation_broken(trino), state_in)

    sync.assert_not_called()
    assert state_out.deferred == []


def test_trino_credential_rotation_forces_connection_update(ctx):
    """A rotated Trino credentials secret updates every connection."""
    secret = Secret(
        tracked_content=TRINO_CREDENTIALS,
        latest_content=TRINO_CREDENTIALS_NEW,
        owner=None,
    )
    state_in = build_state(
        extra_relations=(trino_catalog_relation(secret.id),),
        secrets=(secret,),
    )

    with mock.patch(
        "relations.trino_catalog.TrinoCatalogRelationHandler.sync_databases"
    ) as sync:
        ctx.run(ctx.on.secret_changed(secret), state_in)

    sync.assert_called_once_with(force_update_credentials=True)


def test_unrelated_secret_change_does_not_force_update(ctx):
    """A secret that is not the Trino one syncs without forcing updates."""
    trino_secret = Secret(tracked_content=TRINO_CREDENTIALS, owner=None)
    other = Secret(tracked_content={"key": "value"}, owner=None)
    state_in = build_state(
        extra_relations=(trino_catalog_relation(trino_secret.id),),
        secrets=(trino_secret, other),
    )

    with mock.patch(
        "relations.trino_catalog.TrinoCatalogRelationHandler.sync_databases"
    ) as sync:
        ctx.run(ctx.on.secret_changed(other), state_in)

    sync.assert_called_once_with(force_update_credentials=False)


def test_unready_database_relation_waits_rather_than_blocks(ctx):
    """A related database that has not published data yet is a wait.

    No operator action gets the charm out of this state, so it is not a
    block.
    """
    state_in = build_state(
        with_database=False,
        extra_relations=(
            Relation(
                "postgresql_db",
                remote_app_name="postgresql-k8s",
                remote_app_data={},
            ),
        ),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "Waiting for relation data: PostgreSQL"
    )


def test_unready_redis_relation_waits_rather_than_blocks(ctx):
    """A related Redis that has not published data yet is a wait."""
    state_in = build_state()

    with mock.patch(
        "charm.Redis.get_redis_relation_data", return_value=(None, None)
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "Waiting for relation data: Redis"
    )


def test_missing_relation_still_blocks(ctx):
    """A relation that was never made needs an operator, so it blocks."""
    state_out = ctx.run(
        ctx.on.config_changed(), build_state(with_database=False)
    )

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: PostgreSQL"
    )


def test_every_missing_relation_is_reported_at_once(ctx):
    """All the relations an operator has to make are named together.

    Reporting them one at a time makes the operator relate, wait for the
    next status, and relate again.
    """
    state_out = ctx.run(
        ctx.on.config_changed(),
        build_state(with_database=False, with_redis=False),
    )

    assert state_out.unit_status == BlockedStatus(
        "Required relations missing: PostgreSQL, Redis"
    )


def test_role_is_not_validated_when_roles_are_unreadable(ctx):
    """An unreadable role table does not block a custom role.

    The table does not exist until Superset has migrated the metadata
    database.
    """
    state_in = build_state(config={"self-registration-role": "Analyst"})

    with mock.patch("charm.query_metadata_database", return_value=[]):
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == ActiveStatus("Status check: UP")
    assert superset_environment(state_out)["SELF_REGISTRATION_ROLE"] == (
        "Analyst"
    )
