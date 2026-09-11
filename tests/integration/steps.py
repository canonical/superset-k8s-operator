#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""The step library the Superset integration scenarios are written from.

Steps are grouped by the clause they belong to. The `deploy_*` steps build the
deployment a scenario starts from, `set_config`, `scale`, `restart_application`,
`refresh_to_local` and `delete_unit_pod` are the actions a scenario performs,
and the `assert_*` and `wait_for_*` steps state what should have come of one.
The rest are readers the assertions are phrased in terms of.
"""

import json
import logging
import shutil
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse

import jubilant
import requests
import yaml
from celery import Celery

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
CHARM_NAME = METADATA["name"]

CHARM_FUNCTIONS = {"app-gunicorn": "ui", "worker": "worker", "beat": "beat"}
UI_NAME = f"{CHARM_NAME}-ui"
WORKER_NAME = f"{CHARM_NAME}-worker"
BEAT_NAME = f"{CHARM_NAME}-beat"
SUPERSET_APPS = (UI_NAME, WORKER_NAME, BEAT_NAME)
SCALABLE_APPS = (UI_NAME, WORKER_NAME)

APPLICATION_PORT = 8088
WORKLOAD_CONTAINER = "superset"
WORKLOAD_SERVICE = "superset"

POSTGRES_NAME = "postgresql-k8s"
POSTGRES_CHANNEL = "14/stable"
REDIS_NAME = "redis-k8s"
REDIS_CHANNEL = "latest/edge"
REDIS_PORT = 6379
REDIS_BROKER_DB = 4  # the Celery broker DB Superset puts its task queue on.

TLS_NAME = "self-signed-certificates"
TLS_CHANNEL = "1/stable"
TRAEFIK_NAME = "traefik-k8s"
TRAEFIK_CHANNEL = "latest/stable"
# Serve Superset at the root of a per-application hostname. The `ingress`
# interface reports whatever URL the provider chooses, and traefik's default
# path-prefix mode would place the UI under /<model>-<app>.
TRAEFIK_CONFIG = {
    "routing_mode": "subdomain",
    "external_hostname": "superset.test",
}
TRAEFIK_DOMAIN = TRAEFIK_CONFIG["external_hostname"]

SMTP_INTEGRATOR_NAME = "smtp-integrator"
SMTP_INTEGRATOR_CHANNEL = "latest/stable"
SMTP_SENDER = "reports@superset.example.com"
SMTP_RECIPIENT = "ops@superset.example.com"
SMTP_CONFIG = {
    "host": "smtp.test",
    "port": 1025,
    "user": "superset",
    "password": "smtp-password",  # nosec B105
    "auth_type": "plain",
    "transport_security": "starttls",
    "domain": "superset.example.com",
    "smtp_sender": SMTP_SENDER,
    "recipients": SMTP_RECIPIENT,
}

OAUTH_INTEGRATOR_NAME = "oauth-external-idp-integrator"
OAUTH_INTEGRATOR_CHANNEL = "latest/edge"
OAUTH_STUB_CONFIG = {
    "issuer_url": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",  # nosec B105
    "introspection_endpoint": "https://oauth2.googleapis.com/tokeninfo",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    "jwks_endpoint": "https://www.googleapis.com/oauth2/v3/certs",
    "scope": "openid email profile",
    "client_id": "stub-google-client-id",
    "client_secret": "stub-google-client-secret",  # nosec B105
}

TRINO_NAME = "trino-k8s"
TRINO_CHANNEL = "latest/edge"

PROMETHEUS_NAME = "prometheus-k8s"
PROMETHEUS_CHANNEL = "2/stable"
PROMETHEUS_PORT = 9090
LOKI_NAME = "loki-k8s"
LOKI_CHANNEL = "2/stable"
LOKI_PORT = 3100
GRAFANA_NAME = "grafana-k8s"
GRAFANA_CHANNEL = "2/stable"
GRAFANA_PORT = 3000
COS_APPS = (PROMETHEUS_NAME, LOKI_NAME, GRAFANA_NAME)

CA_CERT_PATH = "/etc/ssl/certs/charm-ca.pem"
CONFIG_PATH = "/app/pythonpath"
CONFIG_FILES = (
    "superset_config.py",
    "custom_security_manager.py",
    "sentry_interceptor.py",
    "permission_error_messages.py",
)

# The signing keys are user-supplied and shared by every application of one
# deployment, so the suite creates one model secret and grants it to each.
SIGNING_KEYS_SECRET_NAME = "superset-signing-keys"  # nosec B105
SECRET_KEY = "juyIKSS7cFAqJlV"  # nosec
ASYNC_QUERIES_JWT = (
    "18b2f8fcd0d708d270c00508da6e8dfc7a21eff14ea438056809805150439a04"  # nosec
)

API_READY_TIMEOUT = 300
API_READY_INTERVAL = 5

DEPLOY_TIMEOUT = 30 * 60
SETTLE_TIMEOUT = 20 * 60


# --------------------------------------------------------------------------
# Waiting
# --------------------------------------------------------------------------


def _app_status(status: jubilant.Status, app: str) -> Tuple[str, str]:
    """Return an application's workload status and message.

    Args:
        status: The model status.
        app: Application name.

    Returns:
        The status name and message, both empty when the app is absent.
    """
    application = status.apps.get(app)
    if not application:
        return "", ""
    return application.app_status.current, application.app_status.message or ""


def wait_for_status(
    juju: jubilant.Juju,
    expected_by_app: Mapping[str, str | Sequence[str]],
    *,
    timeout: float = SETTLE_TIMEOUT,
    raise_on_error: bool = True,
) -> None:
    """Wait until every named application settles on an expected status.

    Args:
        juju: Jubilant object.
        expected_by_app: Application name to expected status name(s).
        timeout: Maximum seconds to wait.
        raise_on_error: Whether to fail fast on an application in error.
    """
    wanted = {
        app: {expected} if isinstance(expected, str) else set(expected)
        for app, expected in expected_by_app.items()
    }

    wait_kwargs: dict = {"timeout": timeout}
    if raise_on_error:
        wait_kwargs["error"] = lambda status: jubilant.any_error(
            status, *wanted
        )

    juju.wait(
        lambda status: all(
            _app_status(status, app)[0] in expected
            for app, expected in wanted.items()
        )
        and jubilant.all_agents_idle(status, *wanted),
        **wait_kwargs,
    )


def wait_for_active(
    juju: jubilant.Juju,
    apps: Iterable[str],
    *,
    timeout: float = SETTLE_TIMEOUT,
) -> None:
    """Wait until every named application is active and its agents are idle.

    Both halves are needed. An action that runs hooks without changing the
    workload status, such as removing a relation, leaves the application
    active throughout, so waiting on the status alone returns before the hook
    has run and the scenario reads the state the hook was going to change.

    Args:
        juju: Jubilant object.
        apps: Application names to wait for.
        timeout: Maximum seconds to wait.
    """
    app_list = tuple(apps)
    juju.wait(
        lambda status: jubilant.all_active(status, *app_list)
        and jubilant.all_agents_idle(status, *app_list),
        error=lambda status: jubilant.any_error(status, *app_list),
        timeout=timeout,
    )


def wait_for_message(
    juju: jubilant.Juju,
    apps: Iterable[str],
    status_name: str,
    message: str,
    *,
    timeout: float = 10 * 60,
) -> None:
    """Wait until every named application reports a status carrying a message.

    Args:
        juju: Jubilant object.
        apps: Application names to wait for.
        status_name: Expected workload status, e.g. `blocked`.
        message: Substring the status message must contain.
        timeout: Maximum seconds to wait.
    """
    app_list = tuple(apps)

    def _reports(status: jubilant.Status) -> bool:
        """Return True once every application reports the wanted message."""
        for app in app_list:
            current, current_message = _app_status(status, app)
            if current != status_name or message not in current_message:
                return False
        return True

    juju.wait(_reports, timeout=timeout, delay=5, successes=1)


def poll_until(
    juju: jubilant.Juju,
    predicate,
    message: str,
    *,
    timeout: float = 10 * 60,
    delay: float = 10,
) -> None:
    """Poll a predicate until it holds, treating a raised exception as not yet.

    Backends reached over HTTP need time after a relation settles.
    None of that shows in a Juju status, so it is waited on directly.

    Args:
        juju: Jubilant object, used for its polling loop.
        predicate: Called with no arguments; True means ready.
        message: Assertion message used if the predicate never holds.
        timeout: Maximum seconds to wait.
        delay: Seconds between polls.

    Raises:
        AssertionError: If the predicate does not hold before the timeout.
    """

    def _ready(_: jubilant.Status) -> bool:
        """Evaluate the predicate, treating a failed read as not ready."""
        try:
            return predicate()
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a failed read means "not yet"
            logger.info("Check not ready yet: %s", exc)
            return False

    try:
        juju.wait(_ready, timeout=timeout, delay=delay, successes=1)
    except TimeoutError as exc:
        raise AssertionError(message) from exc


# --------------------------------------------------------------------------
# Given: building a deployment
# --------------------------------------------------------------------------


def adopt_or_build(request, juju: jubilant.Juju, build, *args, **kwargs):
    """Build a deployment, or adopt the one in the model under `--no-deploy`.

    Every deployment fixture goes through here, so the `--no-deploy` check has one
    definition rather than one per fixture. It cannot be a pytest marker: a
    marker on a fixture never reaches the test item that is running, so it
    would silently do nothing.

    Args:
        request: Pytest request object.
        juju: The model the deployment is built in.
        build: Called with `juju` and the remaining arguments to build it.
        args: Positional arguments for `build`.
        kwargs: Keyword arguments for `build`.

    Returns:
        The model, holding the deployment either way.
    """
    if request.config.getoption("--no-deploy"):
        logger.info(
            "--no-deploy: adopting the deployment already in '%s' instead of "
            "running %s",
            juju.model,
            build.__name__,
        )
        return juju

    build(juju, *args, **kwargs)
    return juju


def add_signing_keys_secret(juju: jubilant.Juju) -> str:
    """Add the model secret holding this deployment's signing keys.

    Args:
        juju: Jubilant object.

    Returns:
        The secret identifier to set as `signing-keys-secret-id`.
    """
    existing = next(
        (
            secret
            for secret in juju.secrets()
            if SIGNING_KEYS_SECRET_NAME in (secret.name, secret.label)
        ),
        None,
    )
    if existing:
        return str(existing.uri).rsplit(":", maxsplit=1)[-1]

    uri = juju.add_secret(
        name=SIGNING_KEYS_SECRET_NAME,
        content={
            "secret-key": SECRET_KEY,
            "async-queries-jwt": ASYNC_QUERIES_JWT,
        },
    )
    return str(uri).rsplit(":", maxsplit=1)[-1]


def deploy_dependencies(juju: jubilant.Juju) -> None:
    """Deploy the PostgreSQL and Redis the charm cannot start without.

    Args:
        juju: Jubilant object.
    """
    juju.deploy(POSTGRES_NAME, channel=POSTGRES_CHANNEL, trust=True)
    juju.deploy(REDIS_NAME, channel=REDIS_CHANNEL, trust=True)
    wait_for_active(juju, [POSTGRES_NAME, REDIS_NAME], timeout=DEPLOY_TIMEOUT)


def deploy_superset_application(
    juju: jubilant.Juju,
    charm: Path,
    image: str,
    function: str,
    *,
    config: Optional[dict] = None,
    app_name: Optional[str] = None,
    with_signing_keys: bool = True,
) -> str:
    """Deploy one application of a Superset deployment, without relating it.

    The signing keys secret can only be granted once the application exists,
    and the charm blocks until it can read it, so the grant happens here
    rather than in the caller.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        image: The workload OCI image reference.
        function: The `charm-function` value this application fulfils.
        config: Extra charm configuration to deploy with.
        app_name: Application name, derived from the function by default.
        with_signing_keys: Whether to create and grant the signing keys
            secret, which a scenario about the missing-secret block turns off.

    Returns:
        The name the application was deployed under.
    """
    name = app_name or f"{CHARM_NAME}-{CHARM_FUNCTIONS[function]}"
    app_config: dict = {"charm-function": function, "server-alias": UI_NAME}
    if with_signing_keys:
        app_config["signing-keys-secret-id"] = add_signing_keys_secret(juju)
    app_config.update(config or {})

    juju.deploy(
        charm,
        app=name,
        resources={"superset-image": image},
        config=app_config,
        num_units=1,
    )
    if with_signing_keys:
        juju.grant_secret(SIGNING_KEYS_SECRET_NAME, name)
    return name


def integrate_dependencies(juju: jubilant.Juju, app: str) -> None:
    """Integrate an application with PostgreSQL and Redis.

    Args:
        juju: Jubilant object.
        app: Application name.
    """
    juju.integrate(f"{app}:postgresql_db", f"{POSTGRES_NAME}:database")
    juju.integrate(f"{app}:redis", f"{REDIS_NAME}:redis")


def deploy_superset(
    juju: jubilant.Juju,
    charm: Path,
    image: str,
    *,
    functions: Iterable[str] = tuple(CHARM_FUNCTIONS),
    config: Optional[dict] = None,
) -> Tuple[str, ...]:
    """Deploy a complete Superset deployment on its dependencies, active.

    Every application is deployed with the charm's own defaults unless a
    scenario asks for more.

    Args:
        juju: Jubilant object.
        charm: Path to the packed charm.
        image: The workload OCI image reference.
        functions: The `charm-function` values to deploy.
        config: Extra charm configuration applied to every application.

    Returns:
        The names of the applications deployed, in the order given.
    """
    deploy_dependencies(juju)

    names = tuple(
        deploy_superset_application(
            juju, charm, image, function, config=config
        )
        for function in functions
    )

    ui = f"{CHARM_NAME}-{CHARM_FUNCTIONS['app-gunicorn']}"
    if ui in names:
        integrate_dependencies(juju, ui)
        wait_for_active(juju, [ui], timeout=DEPLOY_TIMEOUT)

    for name in names:
        if name != ui:
            integrate_dependencies(juju, name)

    wait_for_active(juju, names, timeout=DEPLOY_TIMEOUT)
    return names


def deploy_traefik(juju: jubilant.Juju, app: str = UI_NAME) -> None:
    """Deploy Traefik and put the UI behind it.

    Args:
        juju: Jubilant object.
        app: The Superset application to expose.
    """
    juju.deploy(
        TRAEFIK_NAME,
        channel=TRAEFIK_CHANNEL,
        config=TRAEFIK_CONFIG,
        trust=True,
    )
    juju.integrate(f"{app}:ingress", f"{TRAEFIK_NAME}:ingress")
    wait_for_active(juju, [TRAEFIK_NAME, app], timeout=SETTLE_TIMEOUT)


def deploy_tls(juju: jubilant.Juju) -> None:
    """Deploy the self-signed certificates provider.

    Args:
        juju: Jubilant object.
    """
    juju.deploy(TLS_NAME, channel=TLS_CHANNEL)
    wait_for_active(juju, [TLS_NAME], timeout=SETTLE_TIMEOUT)


def deploy_smtp_integrator(juju: jubilant.Juju) -> None:
    """Deploy the SMTP provider the `ALERT_REPORTS` feature flag requires.

    No mail server backs it: the charm only publishes the relay details, which
    is all the `smtp` relation carries.

    Args:
        juju: Jubilant object.
    """
    juju.deploy(
        SMTP_INTEGRATOR_NAME,
        channel=SMTP_INTEGRATOR_CHANNEL,
        config=SMTP_CONFIG,
    )
    wait_for_active(juju, [SMTP_INTEGRATOR_NAME], timeout=SETTLE_TIMEOUT)


def deploy_oauth_integrator(juju: jubilant.Juju) -> None:
    """Deploy an OAuth provider carrying a stub identity provider's endpoints.

    Args:
        juju: Jubilant object.
    """
    juju.deploy(
        OAUTH_INTEGRATOR_NAME,
        channel=OAUTH_INTEGRATOR_CHANNEL,
        config=OAUTH_STUB_CONFIG,
    )


def deploy_cos(juju: jubilant.Juju) -> None:
    """Deploy Prometheus, Loki and Grafana and settle them.

    Args:
        juju: Jubilant object.
    """
    juju.deploy(PROMETHEUS_NAME, channel=PROMETHEUS_CHANNEL, trust=True)
    juju.deploy(LOKI_NAME, channel=LOKI_CHANNEL, trust=True)
    juju.deploy(GRAFANA_NAME, channel=GRAFANA_CHANNEL, trust=True)
    wait_for_active(juju, COS_APPS, timeout=DEPLOY_TIMEOUT)


# --------------------------------------------------------------------------
# When: acting on the deployment
# --------------------------------------------------------------------------


def set_config(
    juju: jubilant.Juju,
    apps: Iterable[str],
    config: Mapping[str, Any],
    *,
    wait_for: str = "active",
    timeout: float = SETTLE_TIMEOUT,
) -> None:
    """Apply configuration to applications and wait for them to settle.

    Args:
        juju: Jubilant object.
        apps: Application names to configure.
        config: The configuration to set.
        wait_for: Workload status to settle on, or empty to not wait.
        timeout: Maximum seconds to wait.
    """
    app_list = tuple(apps)
    for app in app_list:
        juju.config(app, dict(config))

    if wait_for:
        wait_for_status(
            juju,
            {app: wait_for for app in app_list},
            timeout=timeout,
            raise_on_error=False,
        )


def scale(juju: jubilant.Juju, app: str, units: int) -> None:
    """Scale an application to an exact number of units and wait for it.

    Args:
        juju: Jubilant object.
        app: Application to scale.
        units: The number of units the application should end up with.
    """
    current = len(juju.status().apps[app].units)
    if units > current:
        juju.add_unit(app, num_units=units - current)
    elif units < current:
        juju.remove_unit(app, num_units=current - units)

    juju.wait(
        lambda status: jubilant.all_active(status, app)
        and len(status.apps[app].units) == units,
        error=lambda status: jubilant.any_error(status, app),
        timeout=DEPLOY_TIMEOUT,
    )


def delete_unit_pod(juju: jubilant.Juju, unit: str) -> None:
    """Delete a unit's pod so Kubernetes reschedules it.

    This is the reschedule a charm has to survive without losing state: the
    container filesystem is wiped and the pebble plan is gone, so everything
    the workload needs has to be re-derived by the charm.

    Args:
        juju: Jubilant object.
        unit: Name of the unit, e.g. `superset-k8s-ui/0`.
    """
    model = model_short_name(juju.model or "")
    _kubectl("delete", "pod", unit.replace("/", "-"), "-n", model)


def _kubectl(*args: str) -> str:
    """Run a kubectl command against the cluster the model runs on.

    Args:
        args: Arguments to pass to kubectl.

    Returns:
        Standard output from the command.

    Raises:
        RuntimeError: If no kubectl client can be found.
    """
    if shutil.which("kubectl"):
        command = ["kubectl", *args]
    elif shutil.which("k8s"):
        command = ["k8s", "kubectl", *args]
    else:
        raise RuntimeError("no kubectl client found on the test runner")

    return subprocess.check_output(  # nosec B603
        command, stderr=subprocess.PIPE, universal_newlines=True
    )


def remove_relation(
    juju: jubilant.Juju,
    requirer: str,
    endpoint: str,
    provider: str,
    provider_endpoint: str,
    *,
    timeout: float = 5 * 60,
) -> None:
    """Remove a relation and wait until Juju has finished destroying it.

    `juju remove-relation` returns before the relation object is gone, and the
    charm reports the relation missing as soon as `relation-broken` runs, so a
    scenario that waits on the status alone can re-integrate too early and be
    refused with "relation is dying, but not yet removed (already exists)".

    Args:
        juju: Jubilant object.
        requirer: The requiring application.
        endpoint: The requirer's endpoint name.
        provider: The providing application.
        provider_endpoint: The provider's endpoint name.
        timeout: Maximum seconds to wait for the relation to disappear.
    """
    juju.remove_relation(
        f"{requirer}:{endpoint}", f"{provider}:{provider_endpoint}"
    )

    def _gone() -> bool:
        """Return True once the relation is absent from the requirer."""
        app = juju.status().apps.get(requirer)
        if app is None:
            return True
        related = app.relations.get(endpoint) or []
        return all(entry.related_app != provider for entry in related)

    poll_until(
        juju,
        _gone,
        f"{requirer}:{endpoint} to {provider} was still present after "
        f"{timeout}s",
        timeout=timeout,
        delay=5,
    )


def restart_application(juju: jubilant.Juju, app: str = UI_NAME) -> None:
    """Run the restart action on an application's first unit.

    Args:
        juju: Jubilant object.
        app: Application name.
    """
    juju.run(f"{app}/0", "restart", wait=5 * 60)


def refresh_to_local(
    juju: jubilant.Juju, app: str, charm: Path, image: str
) -> None:
    """Refresh a deployed application onto the locally built charm and image.

    Args:
        juju: Jubilant object.
        app: Application name.
        charm: Path to the packed charm.
        image: The workload OCI image reference.
    """
    juju.refresh(app, path=charm, resources={"superset-image": image})


# --------------------------------------------------------------------------
# Then: reading the deployment back
# --------------------------------------------------------------------------


def assert_active(juju: jubilant.Juju, apps: Iterable[str]) -> None:
    """Assert every named application is active.

    Args:
        juju: Jubilant object.
        apps: Application names.
    """
    status = juju.status()
    for app in apps:
        current, message = _app_status(status, app)
        assert current == "active", f"{app} is {current}: {message}"


def assert_blocked_with(
    juju: jubilant.Juju,
    apps: Iterable[str],
    message: str,
    *,
    timeout: float = 10 * 60,
) -> None:
    """Assert every named application blocks with a message.

    Args:
        juju: Jubilant object.
        apps: Application names.
        message: Substring the blocked message must contain.
        timeout: Maximum seconds to wait for the message.
    """
    _assert_status_with(juju, apps, "blocked", message, timeout=timeout)


def assert_waiting_with(
    juju: jubilant.Juju,
    apps: Iterable[str],
    message: str,
    *,
    timeout: float = 10 * 60,
) -> None:
    """Assert every named application waits with a message.

    Args:
        juju: Jubilant object.
        apps: Application names.
        message: Substring the waiting message must contain.
        timeout: Maximum seconds to wait for the message.
    """
    _assert_status_with(juju, apps, "waiting", message, timeout=timeout)


def _assert_status_with(
    juju: jubilant.Juju,
    apps: Iterable[str],
    status_name: str,
    message: str,
    *,
    timeout: float,
) -> None:
    """Assert every named application reports a status carrying a message.

    Args:
        juju: Jubilant object.
        apps: Application names.
        status_name: Expected workload status.
        message: Substring the status message must contain.
        timeout: Maximum seconds to wait.

    Raises:
        AssertionError: If the status is not reached before the timeout.
    """
    app_list = tuple(apps)
    try:
        wait_for_message(juju, app_list, status_name, message, timeout=timeout)
    except TimeoutError:
        status = juju.status()
        reported = {app: _app_status(status, app) for app in app_list}
        raise AssertionError(
            f"expected every app in {list(app_list)} to be {status_name} "
            f"with {message!r}, got {reported}"
        ) from None


def model_short_name(model_name: str) -> str:
    """Return a model name without its controller prefix.

    Args:
        model_name: Full model name, possibly controller-prefixed.

    Returns:
        The model name alone.
    """
    if ":" in model_name:
        return model_name.split(":", maxsplit=1)[1]
    return model_name


def get_unit_url(
    juju: jubilant.Juju,
    app: str,
    unit: int = 0,
    port: int = APPLICATION_PORT,
    protocol: str = "http",
) -> str:
    """Return the URL a unit serves on.

    Args:
        juju: Jubilant object.
        app: Application name.
        unit: Unit number.
        port: Port the workload serves on.
        protocol: Transfer protocol.

    Returns:
        A URL of the form {protocol}://{address}:{port}.

    Raises:
        ValueError: If no address is published for the unit.
    """
    status = juju.status()
    app_status = status.apps[app]
    unit_status = app_status.units.get(f"{app}/{unit}")

    address = ""
    if unit_status:
        address = unit_status.address or unit_status.public_address
    if not address:
        raise ValueError(
            f"no unit address published for {app}/{unit}; the application "
            f"address {app_status.address!r} is a ClusterIP and is not usable"
        )

    return f"{protocol}://{address}:{port}"


def get_admin_password(
    juju: jubilant.Juju, app: str = UI_NAME, unit: int = 0
) -> str:
    """Return the admin password the charm generated for an application.

    Args:
        juju: Jubilant object.
        app: Application name.
        unit: Unit number to run the action on.

    Returns:
        The admin password.

    Raises:
        ValueError: If the action returns no password.
    """
    task = juju.run(f"{app}/{unit}", "get-admin-password", wait=5 * 60)
    password = task.results.get("password", "")
    if not password:
        raise ValueError(f"get-admin-password returned no password: {task}")
    return password


def read_workload_file(
    juju: jubilant.Juju, unit: str, path: str
) -> Tuple[int, str]:
    """Read a file from the Superset workload container.

    Args:
        juju: Jubilant object.
        unit: Name of the unit, e.g. `superset-k8s-ui/0`.
        path: Absolute path of the file inside the container.

    Returns:
        The command return code and the file contents.
    """
    try:
        return 0, juju.ssh(unit, "cat", path, container=WORKLOAD_CONTAINER)
    except jubilant.CLIError as exc:
        return exc.returncode or 1, exc.stdout or ""


def workload_environment(juju: jubilant.Juju, unit: str) -> dict[str, str]:
    """Return the environment of the charm-managed Superset pebble service.

    Charm-set variables live in the Pebble service environment, which an exec
    shell does not inherit, so they are read from the rendered plan.

    Args:
        juju: Jubilant object.
        unit: Name of the unit, e.g. `superset-k8s-worker/0`.

    Returns:
        The service environment, with every value rendered as a string.
    """
    task = juju.exec(
        "PEBBLE_SOCKET=/charm/containers/superset/pebble.socket "
        "/charm/bin/pebble plan",
        unit=unit,
        wait=5 * 60,
    )
    plan = yaml.safe_load(task.stdout)
    environment = plan["services"][WORKLOAD_SERVICE]["environment"]
    return {
        key: "" if value is None else str(value)
        for key, value in environment.items()
    }


def workload_services(juju: jubilant.Juju, unit: str) -> dict[str, str]:
    """Return the pebble service names and their current state on a unit.

    Args:
        juju: Jubilant object.
        unit: Name of the unit, e.g. `superset-k8s-worker/0`.

    Returns:
        Mapping of service name to its current pebble state.
    """
    task = juju.exec(
        "PEBBLE_SOCKET=/charm/containers/superset/pebble.socket "
        "/charm/bin/pebble services",
        unit=unit,
        wait=5 * 60,
    )
    services = {}
    for line in task.stdout.splitlines()[1:]:
        name, _startup, current, *_since = line.split()
        services[name] = current
    return services


def api_session(
    juju: jubilant.Juju, app: str = UI_NAME, unit: int = 0
) -> Tuple[requests.Session, str]:
    """Authenticate with the Superset API and return a session and its URL.

    Args:
        juju: Jubilant object.
        app: Application serving the API.
        unit: Unit number to talk to.

    Returns:
        The authenticated session and the base URL it is bound to.
    """
    base_url = get_unit_url(juju, app, unit)
    session = requests.Session()
    auth_payload = {
        "username": "admin",
        "password": get_admin_password(juju, app),
        "provider": "db",
    }
    access_token = None

    def _logged_in() -> bool:
        """Return True once the API accepts the admin's credentials."""
        nonlocal access_token
        response = session.post(
            f"{base_url}/api/v1/security/login",
            json=auth_payload,
            timeout=30,
        )
        access_token = response.json().get("access_token")
        return bool(access_token)

    poll_until(
        juju,
        _logged_in,
        f"Superset API at {base_url} did not accept a login within "
        f"{API_READY_TIMEOUT}s",
        timeout=API_READY_TIMEOUT,
        delay=API_READY_INTERVAL,
    )

    session.headers.update(
        {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
    )

    csrf_url = f"{base_url}/api/v1/security/csrf_token/"
    csrf_response = session.get(csrf_url, timeout=30)
    csrf_response.raise_for_status()
    session.headers.update(
        {
            "Referer": csrf_url,
            "X-CSRF-Token": csrf_response.json().get("result"),
        }
    )
    return session, base_url


def api_post(
    session: requests.Session, url: str, path: str, data: dict
) -> int:
    """Create a Superset resource and return its identifier.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        path: API resource path.
        data: Resource payload.

    Returns:
        The created resource identifier.
    """
    response = session.post(f"{url}{path}", json=data, timeout=30)
    assert (
        response.ok
    ), f"POST {path} failed ({response.status_code}): {response.text}"
    return response.json()["id"]


def api_delete(
    session: requests.Session, url: str, path: str, resource_id: int
) -> None:
    """Delete a Superset resource, tolerating a prior cleanup attempt.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        path: API resource path.
        resource_id: Resource identifier to remove.
    """
    response = session.delete(f"{url}{path}/{resource_id}", timeout=30)
    assert response.status_code in (200, 404), response.text


def chart_names(session: requests.Session, url: str) -> set[str]:
    """Return the names of every chart the deployment holds.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.

    Returns:
        The set of chart names.
    """
    response = session.get(f"{url}/api/v1/chart/", timeout=30)
    response.raise_for_status()
    return {chart["slice_name"] for chart in response.json()["result"]}


def create_metadata_backed_chart(
    session: requests.Session,
    url: str,
    environment: Mapping[str, str],
    name: str,
) -> Tuple[int, int, int]:
    """Create a chart on a data source every unit of the deployment can reach.

    Superset's bundled examples live in a SQLite file written during UI
    bootstrap, so they exist only on the UI unit's filesystem and no worker
    can ever query them. The Superset metadata database is reachable from
    every unit, so it backs the chart here.

    Args:
        session: Authenticated Superset API session.
        url: Superset base URL.
        environment: A workload environment carrying `SQL_ALCHEMY_URI`.
        name: The chart name to create.

    Returns:
        The database, dataset and chart identifiers, in that order.
    """
    database_id = api_post(
        session,
        url,
        "/api/v1/database/",
        {
            "database_name": f"superset-metadata-{name}",
            "sqlalchemy_uri": environment["SQL_ALCHEMY_URI"],
            "expose_in_sqllab": True,
        },
    )
    dataset_id = api_post(
        session,
        url,
        "/api/v1/dataset/",
        {
            "database": database_id,
            "schema": "public",
            "table_name": "ab_user",
        },
    )
    chart_id = api_post(
        session,
        url,
        "/api/v1/chart/",
        {
            "slice_name": name,
            "viz_type": "big_number_total",
            "datasource_id": dataset_id,
            "datasource_type": "table",
            "params": json.dumps(
                {
                    "datasource": f"{dataset_id}__table",
                    "viz_type": "big_number_total",
                    "metric": "count",
                    "adhoc_filters": [],
                    "time_range": "No filter",
                }
            ),
        },
    )
    return database_id, dataset_id, chart_id


def celery_app(juju: jubilant.Juju) -> Celery:
    """Return a Celery client bound to the deployment's broker.

    Args:
        juju: Jubilant object.

    Returns:
        A Celery application pointed at the Redis broker Superset uses.
    """
    status = juju.status()
    redis_address = status.apps[REDIS_NAME].units[f"{REDIS_NAME}/0"].address
    return Celery(
        "superset",
        broker=f"redis://{redis_address}:{REDIS_PORT}/{REDIS_BROKER_DB}",
    )


def celery_workers(juju: jubilant.Juju) -> dict:
    """Return the workers that answer a control ping on the broker.

    Args:
        juju: Jubilant object.

    Returns:
        Mapping of worker hostname to its ping reply, empty when none answer.
    """
    return celery_app(juju).control.inspect(timeout=10).ping() or {}


def wait_for_celery_workers(
    juju: jubilant.Juju, count: int, *, timeout: float = 10 * 60
) -> dict:
    """Wait until an exact number of Celery workers answer on the broker.

    The wait is performed by `poll_until`, which fails the scenario if the
    count is never reached.

    Args:
        juju: Jubilant object.
        count: The number of workers expected to answer.
        timeout: Maximum seconds to wait.

    Returns:
        The workers that answered.
    """
    workers: dict = {}

    def _answered() -> bool:
        """Return True once the expected number of workers answer."""
        nonlocal workers
        workers = celery_workers(juju)
        return len(workers) == count

    poll_until(
        juju,
        _answered,
        f"expected {count} Celery workers on the broker, got {workers}",
        timeout=timeout,
    )
    return workers


def request_until(
    session: Optional[requests.Session],
    method: str,
    url: str,
    *,
    expected_status: int | Iterable[int] = 200,
    attempts: int = 30,
    delay: float = 10.0,
    timeout: float = 30.0,
    **kwargs: Any,
) -> requests.Response:
    """Issue an HTTP request, retrying until it returns an expected status.

    Args:
        session: A session to carry cookies across calls, or None.
        method: HTTP method.
        url: Target URL.
        expected_status: Status code, or codes, to wait for.
        attempts: Maximum number of attempts.
        delay: Seconds between attempts.
        timeout: Per-request timeout.
        kwargs: Forwarded to requests.

    Returns:
        The first response carrying an expected status, or the last seen.

    Raises:
        AssertionError: If every attempt raised a connection error.
    """
    wanted = (
        frozenset([expected_status])
        if isinstance(expected_status, int)
        else frozenset(expected_status)
    )
    requester = session.request if session is not None else requests.request
    last_response: Optional[requests.Response] = None
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            response = requester(method, url, timeout=timeout, **kwargs)
        except requests.RequestException as exc:
            last_error = exc
            logger.info(
                "%s %s failed: %s (attempt %d/%d)",
                method,
                url,
                exc,
                attempt,
                attempts,
            )
        else:
            if response.status_code in wanted:
                return response
            last_response = response
            logger.info(
                "%s %s -> %d, want one of %s (attempt %d/%d)",
                method,
                url,
                response.status_code,
                sorted(wanted),
                attempt,
                attempts,
            )
        time.sleep(delay)

    if last_response is not None:
        return last_response
    raise AssertionError(f"{method} {url} never answered: {last_error}")


def assert_ui_serves(
    juju: jubilant.Juju, app: str = UI_NAME, unit: int = 0
) -> None:
    """Assert the Superset UI answers a request on a unit.

    Args:
        juju: Jubilant object.
        app: Application name.
        unit: Unit number.
    """
    url = get_unit_url(juju, app, unit)
    response = request_until(None, "GET", url)
    assert (
        response.status_code == 200
    ), f"{url} returned {response.status_code}: {response.text[:200]}"


def proxied_url(juju: jubilant.Juju, requirer: str = UI_NAME) -> str:
    """Return the ingress URL Traefik published to one of its requirers.

    In `subdomain` routing mode this is the per-application hostname Traefik
    routes on, and it is what the charm derives its external URL and its OAuth
    callback from.

    Args:
        juju: Jubilant object.
        requirer: The application whose ingress URL is wanted.

    Returns:
        The URL published to that requirer.
    """
    data = published_relation_data(juju, f"{requirer}/0", "ingress", "ingress")
    return json.loads(data["ingress"])["url"]


def assert_ingress_serves(
    juju: jubilant.Juju, requirer: str = UI_NAME
) -> None:
    """Assert the URL Traefik publishes actually reaches the requirer.

    Superset answers `/` with a redirect to its login page, and that redirect
    carries Superset's own headers, so any 2xx or 3xx is the requirer having
    been reached. A 404 is Traefik matching no route.

    Args:
        juju: Jubilant object.
        requirer: The application behind the ingress.
    """
    url = proxied_url(juju, requirer)
    hostname = urlparse(url).hostname or ""
    assert hostname.endswith(
        TRAEFIK_DOMAIN
    ), f"expected an ingress URL under {TRAEFIK_DOMAIN}, got {url}"

    response = request_until(
        None,
        "GET",
        get_unit_url(juju, TRAEFIK_NAME, 0, 80),
        expected_status=range(200, 400),
        attempts=12,
        timeout=15,
        headers={"Host": hostname},
        allow_redirects=False,
    )
    assert (
        200 <= response.status_code < 400
    ), f"{url} returned {response.status_code}: {response.text[:200]}"


def published_relation_data(
    juju: jubilant.Juju, remote_unit: str, endpoint: str, field: str
) -> dict[str, Any]:
    """Return the application databag published to a unit on one relation.

    `juju show-unit` reports the REMOTE application's databag, so to read what
    an application published, the unit on the other end of the relation is the
    one to ask. The endpoint can admit several relations, so the one carrying
    the wanted field is the one returned.

    Args:
        juju: Jubilant object.
        remote_unit: The unit to read, e.g. `oauth-external-idp-integrator/0`.
        endpoint: The relation endpoint name on that unit.
        field: A field the wanted databag is known to carry.

    Returns:
        The application databag published to that unit.

    Raises:
        AssertionError: If no relation on the endpoint carries the field.
    """
    for relation in juju.show_unit(remote_unit).relation_info:
        if relation.endpoint == endpoint and field in relation.app_data:
            return relation.app_data
    raise AssertionError(
        f"no {endpoint} relation on {remote_unit} carries {field!r}"
    )
