# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals and Scenario state builders for the Superset K8s charm unit tests."""

from ops.testing import (
    CheckInfo,
    Container,
    Exec,
    Model,
    PeerRelation,
    Relation,
    Secret,
    State,
)

SERVER_PORT = "8088"
MODEL_NAME = "superset-model"
SECRET_KEY = "example-pass"  # nosec B105

CA_PEM = (
    "-----BEGIN CERTIFICATE-----\nMIIBexample\n-----END CERTIFICATE-----\n"
)

INCOMPLETE_PEBBLE_PLAN = {"services": {"superset": {"override": "replace"}}}

OAUTH_PROVIDER_DATA = {
    "issuer_url": "https://idp.example",
    "authorization_endpoint": "https://idp.example/authorize",
    "token_endpoint": "https://idp.example/token",  # nosec B105
    "introspection_endpoint": "https://idp.example/introspect",
    "userinfo_endpoint": "https://idp.example/userinfo",
    "jwks_endpoint": "https://idp.example/jwks",
    "scope": "openid email profile",
    "client_id": "superset-client",
}

SMTP_SECRET_CONTENTS = {
    "host": "localhost",
    "port": "1025",
    "username": "admin",  # nosec B105
    "password": "testpassword",  # nosec B105
    "email": "admin@example.com",
    "ssl": "false",
    "starttls": "false",
    "ssl-server-auth": "false",
    "superset-external-url": "superset.com",
    "email-subject-prefix": "[Test] ",
}

GOOGLE_OAUTH_PROVIDER_DATA = {
    "issuer_url": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",  # nosec B105
    "introspection_endpoint": "https://oauth2.googleapis.com/tokeninfo",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    "jwks_endpoint": "https://www.googleapis.com/oauth2/v3/certs",
    "scope": "openid email profile",
    "client_id": "google-client-id",
}


def database_provider_databag():
    """Create and return mock database info.

    Returns:
        Relation databag published by the PostgreSQL provider.
    """
    return {
        "endpoints": "myhost:5432,anotherhost:2345",
        "username": "postgres_user",  # nosec B105
        "password": "admin",  # nosec B105
    }


def superset_container(*, check_status=None, exec_return_code=0):
    """Build the Superset workload container.

    Args:
        check_status: status to report for the `up` pebble check, or None to
            declare no checks at all.
        exec_return_code: return code for `update-ca-certificates`.

    Returns:
        A Scenario `Container`.
    """
    check_infos = set()
    if check_status is not None:
        check_infos = {CheckInfo("up", status=check_status)}
    return Container(
        "superset",
        can_connect=True,
        check_infos=check_infos,
        execs={Exec(["update-ca-certificates"], return_code=exec_return_code)},
    )


def build_state(
    *,
    leader=True,
    config=None,
    container=None,
    extra_relations=(),
    secrets=(),
    with_database=True,
    with_redis=True,
):
    """Build the input `State` for a healthy Superset UI application.

    Args:
        leader: whether the unit is the leader.
        config: extra config options merged over the defaults.
        container: an explicit container to use instead of the default.
        extra_relations: additional relations to include in the state.
        secrets: secrets to include in the state.
        with_database: whether to include the PostgreSQL relation.
        with_redis: whether to include the Redis relation.

    Returns:
        A Scenario `State`.
    """
    relations = {PeerRelation("peer")}
    if with_database:
        relations.add(
            Relation(
                "postgresql_db",
                remote_app_name="postgresql-k8s",
                remote_app_data=database_provider_databag(),
            )
        )
    if with_redis:
        relations.add(
            Relation(
                "redis",
                remote_app_name="redis-k8s",
                remote_units_data={0: {}},
            )
        )
    relations.update(extra_relations)

    base_config = {"superset-secret-key": SECRET_KEY}
    if config:
        base_config.update(config)

    return State(
        leader=leader,
        model=Model(name=MODEL_NAME),
        config=base_config,
        containers={container or superset_container()},
        relations=relations,
        secrets=set(secrets),
    )


def oauth_secret(secret_value="secret-value", latest_value=None):  # nosec B107
    """Build the OAuth client secret published by the provider.

    Args:
        secret_value: the client secret the charm has already observed.
        latest_value: a newer client secret the provider has since set, or
            None when the secret has not been rotated.

    Returns:
        A Scenario `Secret` owned by the OAuth provider.
    """
    latest = {"secret": latest_value} if latest_value else None
    return Secret(
        tracked_content={"secret": secret_value},
        latest_content=latest,
        owner=None,
    )


def oauth_relation(secret_id=None):
    """Build an OAuth relation carrying complete provider data.

    Args:
        secret_id: id of the Juju secret holding the client secret, or None to
            leave the registration incomplete.

    Returns:
        A Scenario `Relation`.
    """
    remote_data = dict(OAUTH_PROVIDER_DATA)
    if secret_id is not None:
        remote_data["client_secret_id"] = secret_id
    return Relation(
        "oauth",
        remote_app_name="hydra",
        remote_app_data=remote_data,
    )


def google_oauth_relation(secret_id=None):
    """Build an oauth relation whose provider is Google-backed.

    Simulates an oauth-external-idp-integrator deployment configured for
    Google (per datahub-mcp-k8s-operator's README) and related exactly like
    Hydra -- same relation, same interface, just a different provider.

    Args:
        secret_id: id of the Juju secret holding the client secret, or None to
            leave the registration incomplete.

    Returns:
        A Scenario `Relation`.
    """
    remote_data = dict(GOOGLE_OAUTH_PROVIDER_DATA)
    if secret_id is not None:
        remote_data["client_secret_id"] = secret_id
    return Relation(
        "oauth",
        remote_app_name="oauth-external-idp-integrator",
        remote_app_data=remote_data,
    )


def ingress_relation(url="https://superset.example"):
    """Build an ingress relation with a URL published by the provider.

    Args:
        url: the external URL the provider reports, or None to model a
            related-but-not-yet-ready provider.

    Returns:
        A Scenario `Relation`.
    """
    remote_data = {} if url is None else {"ingress": f'{{"url": "{url}"}}'}
    return Relation(
        "ingress",
        remote_app_name="traefik-k8s",
        remote_app_data=remote_data,
    )


def mcp_ingress_relation(external_host="traefik.example"):
    """Build an mcp-ingress (traefik-route) relation with Traefik's host.

    Unlike ingress_relation(), the traefik-route interface has the
    *provider* (Traefik) publish only its own root external_host/scheme;
    the charm derives the actual per-app MCP hostname from that (see
    SupersetK8SCharm._mcp_route_host).

    Args:
        external_host: the root hostname Traefik reports, or None to model
            a related-but-not-yet-ready provider.

    Returns:
        A Scenario `Relation`.
    """
    remote_data = (
        {}
        if external_host is None
        else {"external_host": external_host, "scheme": "https"}
    )
    return Relation(
        "mcp-ingress",
        remote_app_name="traefik-k8s",
        remote_app_data=remote_data,
    )


def superset_environment(state):
    """Return the rendered Superset service environment.

    Args:
        state: the Scenario `State` to read the plan from.

    Returns:
        The environment mapping of the `superset` pebble service.
    """
    return state.get_container("superset").plan.to_dict()["services"][
        "superset"
    ]["environment"]


def mcp_environment(state):
    """Return the rendered MCP service environment.

    Args:
        state: the Scenario `State` to read the plan from.

    Returns:
        The environment mapping of the `mcp` pebble service.
    """
    return state.get_container("superset").plan.to_dict()["services"]["mcp"][
        "environment"
    ]
