#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: database connections built from a Trino provider's catalogs.

The `trino-catalog` relation carries the catalogs a Trino deployment serves,
and the charm turns each of them into a Superset database connection. It only
ever creates and refreshes: a catalog that goes away, and the relation itself
going away, leave the connections they produced in place, because a
connection may carry configs and dashboards the operator still wants.
"""

import logging
import textwrap
from pathlib import Path

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

BACKENDS = textwrap.dedent("""\
    backends:
      dwh:
        connector: postgresql
        url: jdbc:postgresql://example.com:5432
      mysql:
        connector: mysql
        url: jdbc:mysql://example.com:3306
      redshift:
        connector: redshift
        url: jdbc:redshift://example.com:5439/example
""")

CATALOG_BACKENDS = {
    "pgsql": "dwh",
    "mysql": "mysql",
    "redshift": "redshift",
}
CATALOG_SECRETS = {
    "pgsql": "postgresql-secret",  # nosec B105
    "mysql": "mysql-secret",  # nosec B105
    "redshift": "redshift-secret",  # nosec B105
}
REPLICA_SECRETS = {
    "pgsql": "rw:\n  user: trino\n  password: pwd1\n  suffix: _developer\n",  # nosec B105
    "mysql": "ro:\n  user: trino_ro\n  password: pwd3\n",  # nosec B105
    "redshift": "ro:\n  user: trino_ro\n  password: pwd4\n",  # nosec B105
}


def build_catalog_config(
    secret_ids: dict[str, str], catalogs: list[str]
) -> str:
    """Build a Trino `catalog-config` declaring exactly the given catalogs.

    Args:
        secret_ids: Catalog name to the identifier of its credentials secret.
        catalogs: Catalog names to declare.

    Returns:
        The `catalog-config` value as a YAML string.
    """
    entries = []
    for name in catalogs:
        entry = [
            f"  {name}:",
            f"    backend: {CATALOG_BACKENDS[name]}",
            f"    secret-id: {secret_ids[name]}",
        ]
        if name == "pgsql":
            entry.insert(2, "    database: example")
        entries.append("\n".join(entry))

    return "catalogs:\n" + "\n".join(entries) + "\n" + BACKENDS


def set_catalogs(
    juju: jubilant.Juju, secret_ids: dict[str, str], catalogs: list[str]
) -> None:
    """Reconfigure Trino to serve exactly the given catalogs.

    Args:
        juju: Jubilant object.
        secret_ids: Catalog name to the identifier of its credentials secret.
        catalogs: Catalog names Trino should declare.
    """
    juju.config(
        steps.TRINO_NAME,
        {"catalog-config": build_catalog_config(secret_ids, catalogs)},
    )
    steps.wait_for_active(
        juju, [steps.TRINO_NAME, steps.UI_NAME], timeout=steps.SETTLE_TIMEOUT
    )


def serve_every_catalog(
    juju: jubilant.Juju, secret_ids: dict[str, str]
) -> None:
    """Reconfigure Trino to serve all three catalogs.

    Args:
        juju: Jubilant object.
        secret_ids: Catalog name to the identifier of its credentials secret.
    """
    secret_ids["redshift"] = add_catalog_secret(juju, "redshift")
    set_catalogs(juju, secret_ids, list(CATALOG_SECRETS))


def add_catalog_secret(juju: jubilant.Juju, catalog: str) -> str:
    """Add the credentials secret one Trino catalog is served with.

    Args:
        juju: Jubilant object.
        catalog: The catalog name.

    Returns:
        The identifier of the secret created.
    """
    existing = existing_catalog_secrets(juju).get(catalog)
    if existing:
        return existing

    uri = juju.add_secret(
        name=CATALOG_SECRETS[catalog],
        content={"replicas": REPLICA_SECRETS[catalog]},
    )
    juju.grant_secret(CATALOG_SECRETS[catalog], steps.TRINO_NAME)
    return str(uri).rsplit(":", maxsplit=1)[-1]


def existing_catalog_secrets(juju: jubilant.Juju) -> dict[str, str]:
    """Return the catalog credentials secrets the model already holds.

    A scenario that adds or withdraws a catalog rewrites the whole
    `catalog-config`, so it needs the identifier of every secret the config
    names, including ones an earlier run created.

    Args:
        juju: Jubilant object.

    Returns:
        Catalog name to the identifier of its credentials secret, holding only
        the catalogs whose secret exists.
    """
    by_name = {
        secret.name
        or secret.label: str(secret.uri).rsplit(":", maxsplit=1)[-1]
        for secret in juju.secrets()
    }
    return {
        catalog: by_name[secret_name]
        for catalog, secret_name in CATALOG_SECRETS.items()
        if secret_name in by_name
    }


def trino_connection_names(juju: jubilant.Juju) -> set[str]:
    """Return the names of every Trino-backed connection Superset holds.

    Args:
        juju: Jubilant object.

    Returns:
        The set of connection names whose backend is trino.
    """
    session, url = steps.api_session(juju)
    response = session.get(f"{url}/api/v1/database/", timeout=30)
    response.raise_for_status()
    return {
        database["database_name"]
        for database in response.json().get("result", [])
        if database.get("backend") == "trino"
    }


def wait_for_connections(
    juju: jubilant.Juju, count: int, *, timeout: float = 10 * 60
) -> set[str]:
    """Wait until Superset holds an exact number of Trino connections.

    Args:
        juju: Jubilant object.
        count: The number of connections expected.
        timeout: Maximum seconds to wait.

    Returns:
        The connection names found.
    """
    names: set[str] = set()

    def _reconciled() -> bool:
        """Return True once the expected number of connections exists."""
        nonlocal names
        names = trino_connection_names(juju)
        return len(names) == count

    steps.poll_until(
        juju,
        _reconciled,
        f"expected {count} Trino connections in Superset, found {names}",
        timeout=timeout,
    )
    return names


@pytest.fixture(scope="module")
def secret_ids() -> dict[str, str]:
    """Record the credentials secret of each catalog as it is created.

    Returns:
        Catalog name to the identifier of its credentials secret, filled in
        as the scenarios declare catalogs.
    """
    return {}


def _deploy_superset_and_trino(
    juju: jubilant.Juju,
    charm: Path,
    charm_image: str,
    secret_ids: dict[str, str],
) -> None:
    """Deploy a Superset UI and a Trino serving two catalogs, related.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
        secret_ids: The mapping the catalog secrets are recorded in.
    """
    juju.deploy(
        steps.TRINO_NAME,
        channel=steps.TRINO_CHANNEL,
        config={"charm-function": "all"},
        trust=True,
    )
    steps.deploy_superset(juju, charm, charm_image, functions=["app-gunicorn"])
    steps.wait_for_active(
        juju, [steps.TRINO_NAME], timeout=steps.DEPLOY_TIMEOUT
    )

    for catalog in ("pgsql", "mysql"):
        secret_ids[catalog] = add_catalog_secret(juju, catalog)
    set_catalogs(juju, secret_ids, ["pgsql", "mysql"])

    juju.integrate(
        f"{steps.TRINO_NAME}:trino-catalog", f"{steps.UI_NAME}:trino-catalog"
    )
    steps.wait_for_active(juju, [steps.TRINO_NAME, steps.UI_NAME])


@pytest.fixture(scope="module")
def superset_related_to_trino(
    request: pytest.FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
    secret_ids: dict[str, str],
) -> jubilant.Juju:
    """Deploy a Superset UI related to a Trino serving two catalogs.

    Under `--no-deploy` the catalog secrets already exist in the model, so
    `secret_ids` is filled in from them rather than from a fresh deploy.

    Args:
        request: Pytest request object.
        model: The module's model.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.
        secret_ids: The mapping the catalog secrets are recorded in.

    Returns:
        The model, with both applications active and related.
    """
    logger.info("Deploying Trino and a Superset UI")
    if request.config.getoption("--no-deploy"):
        secret_ids.update(existing_catalog_secrets(model))
    return steps.adopt_or_build(
        request,
        model,
        _deploy_superset_and_trino,
        charm,
        charm_image,
        secret_ids,
    )


def test_every_catalog_becomes_a_superset_connection(
    superset_related_to_trino: jubilant.Juju,
):
    """Scenario: Superset is related to a Trino that serves two catalogs.

    Given a Trino serving the pgsql and mysql catalogs
    When Superset is related to it
    Then Superset holds one database connection per catalog
    """
    juju = superset_related_to_trino

    with given("a Trino serving the pgsql and mysql catalogs"):
        pass

    with when("Superset is related to it"):
        pass

    with then("Superset holds one database connection per catalog"):
        names = wait_for_connections(juju, 2)
        assert names == {"Pgsql (pgsql)", "Mysql (mysql)"}, names


def test_adding_a_catalog_adds_a_connection(
    superset_related_to_trino: jubilant.Juju, secret_ids: dict[str, str]
):
    """Scenario: a new catalog is added to the Trino deployment.

    Given a Superset holding a connection for each of two Trino catalogs
    When a redshift catalog is added to Trino
    Then Superset gains a connection for it
    """
    juju = superset_related_to_trino

    with given(
        "a Superset holding a connection for each of two Trino catalogs"
    ):
        wait_for_connections(juju, 2)

    with when("a redshift catalog is added to Trino"):
        serve_every_catalog(juju, secret_ids)

    with then("Superset gains a connection for it"):
        names = wait_for_connections(juju, 3)
        assert "Redshift (redshift)" in names, names


def test_removing_a_catalog_leaves_its_connection_alone(
    superset_related_to_trino: jubilant.Juju, secret_ids: dict[str, str]
):
    """Scenario: a catalog is withdrawn from the Trino deployment.

    Given a Superset holding a connection for each of three Trino catalogs
    When the mysql catalog is removed from Trino
    Then its Superset connection is still there
    """
    juju = superset_related_to_trino

    with given(
        "a Superset holding a connection for each of three Trino catalogs"
    ):
        serve_every_catalog(juju, secret_ids)
        assert "Mysql (mysql)" in wait_for_connections(juju, 3)

    with when("the mysql catalog is removed from Trino"):
        set_catalogs(juju, secret_ids, ["pgsql", "redshift"])

    with then("its Superset connection is still there"):
        names = wait_for_connections(juju, 3)
        assert "Mysql (mysql)" in names, names


def test_breaking_the_relation_leaves_every_connection_alone(
    superset_related_to_trino: jubilant.Juju, secret_ids: dict[str, str]
):
    """Scenario: the trino-catalog relation is removed entirely.

    Given a Superset holding three Trino-backed connections
    When the trino-catalog relation is removed
    Then Superset stays active with no trino-catalog relation
    And all three connections are still there
    """
    juju = superset_related_to_trino

    with given("a Superset holding three Trino-backed connections"):
        serve_every_catalog(juju, secret_ids)
        wait_for_connections(juju, 3)

    with when("the trino-catalog relation is removed"):
        steps.remove_relation(
            juju,
            steps.UI_NAME,
            "trino-catalog",
            steps.TRINO_NAME,
            "trino-catalog",
        )
        steps.wait_for_active(juju, [steps.UI_NAME])

    with then("Superset stays active with no trino-catalog relation"):
        relations = juju.status().apps[steps.UI_NAME].relations
        assert "trino-catalog" not in relations, relations

    with and_("all three connections are still there"):
        assert len(wait_for_connections(juju, 3)) == 3
