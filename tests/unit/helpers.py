# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Literals and Scenario state builders for the Superset K8s charm unit tests."""

import json

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
ASYNC_QUERIES_JWT = "example-jwt"  # nosec B105
SIGNING_KEYS = {
    "secret-key": SECRET_KEY,
    "async-queries-jwt": ASYNC_QUERIES_JWT,
}

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

TRINO_CREDENTIALS = {
    "username": "trino",
    "password": "trino-password",  # nosec B105
}

TRINO_CREDENTIALS_NEW = {
    "username": "trino",
    "password": "rotated-trino-password",  # nosec B105
}

SMTP_PROVIDER_DATA = {
    "host": "smtp.example",
    "port": "1025",
    "auth_type": "plain",
    "transport_security": "starttls",
    "skip_ssl_verify": "False",
    "user": "superset",
    "password": "smtp-password",  # nosec B105
    "domain": "example.com",
    "smtp_sender": "reports@example.com",
    "recipients": '["ops@example.com"]',
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


def signing_keys_secret(content=None):
    """Build the user secret holding this deployment's signing keys.

    Args:
        content: secret content, defaulting to both required keys.

    Returns:
        A Scenario `Secret` owned by the user.
    """
    return Secret(
        tracked_content=SIGNING_KEYS if content is None else content,
        owner=None,
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
    signing_keys=None,
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
        signing_keys: the signing keys secret to use, or None for a valid
            one built by `signing_keys_secret`.

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

    keys_secret = (
        signing_keys_secret() if signing_keys is None else signing_keys
    )
    base_config = {"signing-keys-secret-id": keys_secret.id}
    if config:
        base_config.update(config)

    return State(
        leader=leader,
        model=Model(name=MODEL_NAME),
        config=base_config,
        containers={container or superset_container()},
        relations=relations,
        secrets=set(secrets) | {keys_secret},
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


def smtp_relation(password_id=None, **overrides):
    """Build an SMTP relation carrying the relay an integrator publishes.

    Args:
        password_id: id of the Juju secret holding the relay password, which
            replaces the plain `password` field when it is given.
        overrides: relation fields to override or, with a None value, drop.

    Returns:
        A Scenario `Relation`.
    """
    remote_data = dict(SMTP_PROVIDER_DATA)
    if password_id is not None:
        del remote_data["password"]
        remote_data["password_id"] = password_id

    for key, value in overrides.items():
        if value is None:
            remote_data.pop(key, None)
        else:
            remote_data[key] = value

    return Relation(
        "smtp",
        remote_app_name="smtp-integrator",
        remote_app_data=remote_data,
    )


def smtp_password_secret(password="smtp-password"):  # nosec B107
    """Build the relay password secret an SMTP provider grants.

    Args:
        password: the SMTP AUTH password the secret carries.

    Returns:
        A Scenario `Secret` owned by the SMTP provider.
    """
    return Secret(tracked_content={"password": password}, owner=None)


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


def trino_catalog_relation(secret_id, catalogs=("marketing",)):
    """Build a trino-catalog relation carrying complete provider data.

    Args:
        secret_id: id of the Juju secret holding the Trino credentials.
        catalogs: names of the catalogs the provider publishes.

    Returns:
        A Scenario `Relation`.
    """
    return Relation(
        "trino-catalog",
        remote_app_name="trino-k8s",
        remote_app_data={
            "trino_url": "trino.example:443",
            "trino_catalogs": json.dumps(
                [
                    {"name": name, "connector": "postgresql"}
                    for name in catalogs
                ]
            ),
            "trino_credentials_secret_id": secret_id,
        },
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
