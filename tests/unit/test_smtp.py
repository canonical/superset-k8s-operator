# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for the SMTP relation."""

from ops import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Relation

from tests.unit.helpers import (
    SMTP_PROVIDER_DATA,
    build_state,
    ingress_relation,
    smtp_password_secret,
    smtp_relation,
    superset_environment,
)


def test_smtp_relation_maps_the_relay(ctx):
    """A related SMTP provider is rendered into the workload environment."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(smtp_relation(),),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_HOST"] == SMTP_PROVIDER_DATA["host"]
    assert environment["SMTP_PORT"] == int(SMTP_PROVIDER_DATA["port"])
    assert environment["SMTP_USERNAME"] == SMTP_PROVIDER_DATA["user"]
    assert environment["SMTP_PASSWORD"] == SMTP_PROVIDER_DATA["password"]
    assert environment["SMTP_EMAIL"] == SMTP_PROVIDER_DATA["smtp_sender"]
    assert environment["SMTP_STARTTLS"] is True
    assert environment["SMTP_SSL"] is False
    assert environment["SMTP_SSL_SERVER_AUTH"] is True
    assert state_out.unit_status == ActiveStatus("Status check: UP")


def test_smtp_relation_resolves_the_password_secret(ctx):
    """A relay password published as a secret ID is resolved to its value."""
    secret = smtp_password_secret(
        password="secret-relay-password"  # nosec B106
    )
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(smtp_relation(password_id=secret.id),),
        secrets=(secret,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_PASSWORD"] == "secret-relay-password"


def test_smtp_relation_with_tls_transport(ctx):
    """`tls` transport security sets SMTP_SSL rather than SMTP_STARTTLS."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(smtp_relation(transport_security="tls"),),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_SSL"] is True
    assert environment["SMTP_STARTTLS"] is False


def test_smtp_relation_without_transport_security(ctx):
    """`none` transport security sets neither SMTP_SSL nor SMTP_STARTTLS."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(
            smtp_relation(transport_security="none", skip_ssl_verify="True"),
        ),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_SSL"] is False
    assert environment["SMTP_STARTTLS"] is False
    assert environment["SMTP_SSL_SERVER_AUTH"] is False


def test_smtp_relation_without_authentication(ctx):
    """No credentials are rendered when the relay does not authenticate."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(
            smtp_relation(auth_type="none", user=None, password=None),
        ),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert "SMTP_USERNAME" not in environment
    assert "SMTP_PASSWORD" not in environment
    assert environment["SMTP_HOST"] == SMTP_PROVIDER_DATA["host"]


def test_no_smtp_variables_without_a_relation(ctx):
    """No SMTP variables are rendered when no provider is related."""
    state_out = ctx.run(ctx.on.config_changed(), build_state())

    environment = superset_environment(state_out)
    assert "SMTP_HOST" not in environment
    assert "SMTP_PASSWORD" not in environment


def test_alert_reports_without_an_smtp_relation_blocks(ctx):
    """The feature flag alone renders reports that cannot be delivered."""
    state_in = build_state(config={"feature-flags": "ALERT_REPORTS"})

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "ALERT_REPORTS requires an smtp relation"
    )


def test_smtp_relation_without_alert_reports_blocks(ctx):
    """The relation alone publishes a relay the workload never reads."""
    state_in = build_state(extra_relations=(smtp_relation(),))

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "the smtp relation requires the ALERT_REPORTS feature flag"
    )


def test_smtp_relation_without_a_sender_blocks(ctx):
    """Superset has no address to send from without `smtp_sender`."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(smtp_relation(smtp_sender=None),),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == BlockedStatus(
        "the smtp relation is missing smtp_sender, which Superset "
        "sends the mail from"
    )


def test_smtp_relation_with_unusable_data_blocks(ctx):
    """A relay the library refuses to parse is the provider's to fix."""
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(smtp_relation(port="not-a-port"),),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert isinstance(state_out.unit_status, BlockedStatus)
    assert state_out.unit_status.message.startswith(
        "smtp relation data is unusable:"
    )


def test_smtp_relation_without_provider_data_waits(ctx):
    """A provider that has published nothing yet resolves on its own."""
    relation = Relation("smtp", remote_app_name="smtp-integrator")
    state_in = build_state(
        config={"feature-flags": "ALERT_REPORTS"},
        extra_relations=(relation,),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    assert state_out.unit_status == WaitingStatus(
        "Waiting for relation data: SMTP"
    )


def test_external_url_falls_back_to_the_ingress_url(ctx):
    """The UI links report emails at the URL the ingress provider publishes."""
    state_in = build_state(extra_relations=(ingress_relation(),))

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_SUPERSET_EXTERNAL_URL"] == (
        "https://superset.example"
    )


def test_external_url_config_overrides_the_ingress_url(ctx):
    """A worker holds no ingress relation, so the option is the only source."""
    state_in = build_state(
        config={"external-url": "https://reports.example"},
        extra_relations=(ingress_relation(),),
    )

    state_out = ctx.run(ctx.on.config_changed(), state_in)

    environment = superset_environment(state_out)
    assert environment["SMTP_SUPERSET_EXTERNAL_URL"] == (
        "https://reports.example"
    )
