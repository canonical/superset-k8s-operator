# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the trino-catalog relation on the Superset side."""

import logging

import pytest
import pytest_asyncio
import requests
from integration.helpers import (
    POSTGRES_NAME,
    REDIS_NAME,
    SUPERSET_SECRET_KEY,
    api_authentication,
    get_unit_url,
    perform_superset_integrations,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

TRINO_APP = "trino-k8s"
SUPERSET_APP = "superset-k8s"
USER_SECRET_LABEL = "trino-user-management"  # nosec

POSTGRESQL_REPLICA_SECRET = """\
rw:
  user: trino
  password: pwd1
  suffix: _developer
ro:
  user: trino_ro
  password: pwd2
"""  # nosec

MYSQL_REPLICA_SECRET = """\
ro:
  user: trino_ro
  password: pwd3
"""  # nosec

REDSHIFT_REPLICA_SECRET = """\
ro:
  user: trino_ro
  password: pwd4
"""  # nosec

TIMEOUT_DEFAULT = 1000
TIMEOUT_DEPLOY = 2000


def get_trino_databases(
    session: requests.Session, base_url: str
) -> list[dict]:
    """Query Superset API for Trino database connections.

    Args:
        session: Authenticated requests session.
        base_url: Superset base URL.

    Returns:
        List of database dicts where backend is trino.
    """
    resp = session.get(f"{base_url}/api/v1/database/", timeout=30)
    resp.raise_for_status()
    databases = resp.json().get("result", [])
    return [db for db in databases if db.get("backend") == "trino"]


def build_catalog_config(
    catalog_secrets: dict[str, str], catalogs: list[str]
) -> str:
    """Build Trino catalog-config YAML with specified catalogs.

    Args:
        catalog_secrets: Dict mapping catalog names to secret IDs.
        catalogs: List of catalog names to include (pgsql, mysql, redshift).

    Returns:
        YAML string for Trino's catalog-config option.
    """
    catalog_entries = []

    if "pgsql" in catalogs:
        catalog_entries.append(
            f"""  pgsql:
    backend: dwh
    database: example
    secret-id: {catalog_secrets['postgresql']}"""
        )

    if "mysql" in catalogs:
        catalog_entries.append(
            f"""  mysql:
    backend: mysql
    secret-id: {catalog_secrets['mysql']}"""
        )

    if "redshift" in catalogs:
        catalog_entries.append(
            f"""  redshift:
    backend: redshift
    secret-id: {catalog_secrets['redshift']}"""
        )

    backends = """backends:
  dwh:
    connector: postgresql
    url: jdbc:postgresql://example.com:5432
  mysql:
    connector: mysql
    url: jdbc:mysql://example.com:3306
  redshift:
    connector: redshift
    url: jdbc:redshift://example.com:5439/example"""

    return "catalogs:\n" + "\n".join(catalog_entries) + "\n" + backends


@pytest.mark.skip_if_deployed
@pytest_asyncio.fixture(name="deploy-trino-superset", scope="module")
async def deploy_trino_superset(
    ops_test: OpsTest, charm: str, charm_image: str
):
    """Deploy Superset with dependencies and Trino."""
    # Deploy dependencies and Trino in parallel
    await ops_test.model.deploy(POSTGRES_NAME, channel="14", trust=True)
    await ops_test.model.deploy(REDIS_NAME, channel="edge", trust=True)
    await ops_test.model.deploy(
        TRINO_APP,
        channel="latest/edge",
        config={"charm-function": "all"},
        trust=True,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[POSTGRES_NAME, REDIS_NAME, TRINO_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEPLOY,
        )

    # Deploy Superset
    resources = {"superset-image": charm_image}
    superset_config = {
        "charm-function": "app-gunicorn",
        "superset-secret-key": SUPERSET_SECRET_KEY,
        "admin-password": "admin",
    }

    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=SUPERSET_APP,
        config=superset_config,
        num_units=1,
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="blocked",
            raise_on_blocked=False,
            timeout=600,
        )

        # Integrate with postgresql and redis
        await perform_superset_integrations(ops_test, SUPERSET_APP)

        assert (
            ops_test.model.applications[SUPERSET_APP].units[0].workload_status
            == "active"
        )


@pytest_asyncio.fixture(scope="module")
async def secret_ids(ops_test: OpsTest) -> dict[str, str]:
    """Create connector secrets and return their IDs."""
    secrets = {}

    pg_secret = await ops_test.model.add_secret(
        name="postgresql-secret",
        data_args=[f"replicas={POSTGRESQL_REPLICA_SECRET}"],
    )
    secrets["postgresql"] = pg_secret.split(":")[-1]

    mysql_secret = await ops_test.model.add_secret(
        name="mysql-secret",
        data_args=[f"replicas={MYSQL_REPLICA_SECRET}"],
    )
    secrets["mysql"] = mysql_secret.split(":")[-1]

    await ops_test.model.grant_secret("postgresql-secret", TRINO_APP)
    await ops_test.model.grant_secret("mysql-secret", TRINO_APP)

    return secrets


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_01_create_relation(ops_test: OpsTest):
    """Test creating the trino-catalog relation."""
    async with ops_test.fast_forward():
        await ops_test.model.integrate(
            f"{TRINO_APP}:trino-catalog",
            f"{SUPERSET_APP}:trino-catalog",
        )

        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP, SUPERSET_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEFAULT,
        )

    trino_catalog_relations = [
        rel
        for rel in ops_test.model.applications[SUPERSET_APP].relations
        if rel.matches(f"{SUPERSET_APP}:trino-catalog")
    ]
    assert len(trino_catalog_relations) > 0


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_02_configure_trino_catalogs(
    ops_test: OpsTest,
    secret_ids: dict[str, str],  # pylint: disable=redefined-outer-name
):
    """Test configuring Trino with catalogs and external hostname."""
    catalog_config = build_catalog_config(secret_ids, ["pgsql", "mysql"])

    async with ops_test.fast_forward():
        await ops_test.model.applications[TRINO_APP].set_config(
            {
                "external-hostname": "trino.test.local",
                "catalog-config": catalog_config,
            }
        )

        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_03_configure_user_secret(ops_test: OpsTest):
    """Test configuring the Trino user secret and granting it to Superset."""
    users_data = "app-superset-k8s: testpassword"  # nosec

    user_secret = await ops_test.model.add_secret(
        name=USER_SECRET_LABEL,
        data_args=[f"users={users_data}"],
    )
    user_secret_id = user_secret.split(":")[-1]

    await ops_test.model.grant_secret(USER_SECRET_LABEL, TRINO_APP)

    async with ops_test.fast_forward():
        await ops_test.model.applications[TRINO_APP].set_config(
            {"user-secret-id": user_secret_id}
        )

        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

    # Grant to Superset (requirer needs read access)
    await ops_test.model.grant_secret(USER_SECRET_LABEL, SUPERSET_APP)

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_04_verify_databases_created(ops_test: OpsTest):
    """Test that Superset database connections were created for each Trino catalog."""
    url = await get_unit_url(
        ops_test, application=SUPERSET_APP, unit=0, port=8088
    )
    session = await api_authentication(ops_test, url)
    trino_dbs = get_trino_databases(session, url)

    assert len(trino_dbs) == 2, (
        f"Expected 2 Trino databases, got {len(trino_dbs)}: "
        f"{[db.get('database_name') for db in trino_dbs]}"
    )

    db_names = {db["database_name"] for db in trino_dbs}
    expected_names = {"Pgsql (pgsql)", "Mysql (mysql)"}
    assert (
        db_names == expected_names
    ), f"Expected {expected_names}, got {db_names}"

    logger.info("Verified %d Trino databases: %s", len(trino_dbs), db_names)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_05_add_catalog(
    ops_test: OpsTest, secret_ids: dict[str, str]
):  # pylint: disable=redefined-outer-name
    """Test that adding a new catalog creates a new Superset database."""
    redshift_secret = await ops_test.model.add_secret(
        name="redshift-secret",
        data_args=[f"replicas={REDSHIFT_REPLICA_SECRET}"],
    )
    secret_ids["redshift"] = redshift_secret.split(":")[-1]

    await ops_test.model.grant_secret("redshift-secret", TRINO_APP)

    catalog_config = build_catalog_config(
        secret_ids, ["pgsql", "mysql", "redshift"]
    )

    async with ops_test.fast_forward():
        await ops_test.model.applications[TRINO_APP].set_config(
            {"catalog-config": catalog_config}
        )

        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

    url = await get_unit_url(
        ops_test, application=SUPERSET_APP, unit=0, port=8088
    )
    session = await api_authentication(ops_test, url)
    trino_dbs = get_trino_databases(session, url)

    assert len(trino_dbs) == 3, (
        f"Expected 3 Trino databases, got {len(trino_dbs)}: "
        f"{[db.get('database_name') for db in trino_dbs]}"
    )

    db_names = {db["database_name"] for db in trino_dbs}
    assert (
        "Redshift (redshift)" in db_names
    ), f"Expected 'Redshift (redshift)' in {db_names}"

    logger.info("Verified new catalog added: %s", db_names)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_06_credential_rotation(ops_test: OpsTest):
    """Test that updating the user secret triggers credential rotation."""
    await ops_test.juju(
        "secret-set",
        USER_SECRET_LABEL,
        "users=app-superset-k8s: rotatedpassword",  # nosec
    )

    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

    url = await get_unit_url(
        ops_test, application=SUPERSET_APP, unit=0, port=8088
    )
    session = await api_authentication(ops_test, url)
    trino_dbs = get_trino_databases(session, url)

    assert len(trino_dbs) == 3, (
        f"Expected 3 Trino databases after credential rotation, "
        f"got {len(trino_dbs)}"
    )

    logger.info(
        "Verified credential rotation: %d databases intact", len(trino_dbs)
    )


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_07_remove_catalog_no_deletion(
    ops_test: OpsTest,
    secret_ids: dict[str, str],  # pylint: disable=redefined-outer-name
):
    """Test that removing a catalog from Trino does NOT delete the Superset database."""
    catalog_config = build_catalog_config(secret_ids, ["pgsql", "redshift"])

    async with ops_test.fast_forward():
        await ops_test.model.applications[TRINO_APP].set_config(
            {"catalog-config": catalog_config}
        )

        await ops_test.model.wait_for_idle(
            apps=[TRINO_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="active",
            timeout=TIMEOUT_DEFAULT,
        )

    url = await get_unit_url(
        ops_test, application=SUPERSET_APP, unit=0, port=8088
    )
    session = await api_authentication(ops_test, url)
    trino_dbs = get_trino_databases(session, url)

    assert len(trino_dbs) == 3, (
        f"Expected 3 Trino databases (no deletion), got {len(trino_dbs)}: "
        f"{[db.get('database_name') for db in trino_dbs]}"
    )

    db_names = {db["database_name"] for db in trino_dbs}
    assert (
        "Mysql (mysql)" in db_names
    ), f"Mysql database should persist after catalog removal, got {db_names}"

    logger.info("Verified no deletion after catalog removal: %s", db_names)


@pytest.mark.abort_on_fail
@pytest.mark.usefixtures("deploy-trino-superset")
async def test_08_relation_broken(ops_test: OpsTest):
    """Test that breaking the relation does NOT delete Superset databases."""
    async with ops_test.fast_forward():
        await ops_test.juju(
            "remove-relation",
            f"{TRINO_APP}:trino-catalog",
            f"{SUPERSET_APP}:trino-catalog",
        )

        await ops_test.model.wait_for_idle(
            apps=[SUPERSET_APP],
            status="active",
            raise_on_blocked=False,
            timeout=TIMEOUT_DEFAULT,
        )

    trino_catalog_relations = [
        rel
        for rel in ops_test.model.applications[SUPERSET_APP].relations
        if rel.matches(f"{SUPERSET_APP}:trino-catalog")
    ]
    assert len(trino_catalog_relations) == 0

    url = await get_unit_url(
        ops_test, application=SUPERSET_APP, unit=0, port=8088
    )
    session = await api_authentication(ops_test, url)
    trino_dbs = get_trino_databases(session, url)

    assert len(trino_dbs) == 3, (
        f"Expected 3 Trino databases after relation broken, "
        f"got {len(trino_dbs)}"
    )

    logger.info(
        "Verified databases persist after relation broken: %d databases",
        len(trino_dbs),
    )
