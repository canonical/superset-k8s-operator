# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset charm integration test config.

The expensive Given of a charm scenario is the deployment it starts from, so
the worlds live here as fixtures named after the state they leave the model
in. A scenario that needs more than one of them composes them rather than
depending on the order its tests run in.
"""

import hashlib
import logging
import os
import sys
import zipfile
from pathlib import Path

import jubilant
import pytest
import steps
from pytest import FixtureRequest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def charm(request: FixtureRequest) -> Path:
    """Return the path to the charm package to deploy.

    Under `--no-deploy` the package is optional, because the world it would
    have built is already in the model. `test_upgrades.py` is the exception:
    its When is the refresh onto the package, so it has to be supplied there.

    Args:
        request: Pytest request object.

    Returns:
        Path to the packed charm.

    Raises:
        FileNotFoundError: If no charm package can be found.
        ValueError: If the working directory holds more than one.
    """
    charm_file = request.config.getoption("--charm-file")
    if charm_file:
        charm_path = Path(charm_file[0]).expanduser().resolve()
        if not charm_path.exists():
            raise FileNotFoundError(f"Charm does not exist: {charm_path}")
        return charm_path

    charm_path_env = os.environ.get("CHARM_PATH")
    if charm_path_env:
        charm_path = Path(charm_path_env).expanduser().resolve()
        if not charm_path.exists():
            raise FileNotFoundError(f"Charm does not exist: {charm_path}")
        return charm_path

    charm_paths = list(Path(".").glob("*.charm"))
    if not charm_paths:
        if request.config.getoption("--no-deploy"):
            # The world already exists, so most scenarios never open this.
            return Path()
        raise FileNotFoundError("No .charm file in the current directory")
    if len(charm_paths) > 1:
        found = ", ".join(str(path) for path in charm_paths)
        raise ValueError(f"More than one .charm file: {found}")
    charm_path = charm_paths[0].resolve()
    _warn_if_stale(charm_path)
    return charm_path


def _warn_if_stale(charm_path: Path) -> None:
    """Warn when a packed charm no longer matches the working tree.

    A scenario run against a package built before the source it is meant to
    exercise passes or fails on code nobody is looking at. CI packs the charm
    on every run so this never fires there; picking up a stale local build is
    easy, and the failure it produces looks exactly like a charm defect.

    Args:
        charm_path: Path to the packed charm.
    """
    try:
        with zipfile.ZipFile(charm_path) as package:
            names = [
                name
                for name in package.namelist()
                if name.startswith(("src/", "templates/"))
                and name.endswith(".py")
            ]
            stale = [
                name
                for name in sorted(names)
                if not Path(name).exists()
                or hashlib.sha256(package.read(name)).hexdigest()
                != hashlib.sha256(Path(name).read_bytes()).hexdigest()
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning(
            "Could not check %s against the source: %s", charm_path, exc
        )
        return

    if stale:
        logger.warning(
            "%s was built from different source than the working tree. "
            "Repack it with `charmcraft pack` before trusting a result. "
            "Differing: %s",
            charm_path.name,
            ", ".join(stale),
        )


@pytest.fixture(scope="session")
def charm_image(request: FixtureRequest) -> str:
    """Return the workload OCI image built by the CI workflow.

    Args:
        request: Pytest request object.

    Returns:
        The image reference.

    Raises:
        ValueError: If the option was not supplied.
    """
    image = request.config.getoption("--superset-image")
    if not image:
        if request.config.getoption("--no-deploy"):
            # The world already exists, so most scenarios never deploy it.
            return ""
        raise ValueError(
            "--superset-image is required and must name the OCI image"
        )
    return image


def _collect_juju_logs_if_failed(
    request: FixtureRequest, juju: jubilant.Juju
) -> None:
    """Print the model's Juju logs at teardown when a scenario failed.

    Args:
        request: Pytest request object.
        juju: Jubilant object.
    """
    if not request.session.testsfailed:
        return
    logger.info("Collecting Juju logs from model '%s'", juju.model)
    print(juju.debug_log(limit=20000), end="", file=sys.stderr)


def _prepare(juju: jubilant.Juju) -> jubilant.Juju:
    """Set the model options every world depends on.

    Args:
        juju: Jubilant object.

    Returns:
        The same object, configured.
    """
    juju.wait_timeout = steps.DEPLOY_TIMEOUT
    try:
        juju.model_config({"update-status-hook-interval": "60s"})
    except jubilant.CLIError as exc:
        logger.warning("Could not shorten the update-status interval: %s", exc)
    return juju


def _model_for(request: FixtureRequest):
    """Yield the model a world is built in, dumping Juju logs on failure.

    Under `--no-deploy` this is the existing model named by `--model`, or the
    active one, and it is left alone afterwards. Otherwise it is a temporary
    model, destroyed at teardown unless `--keep-models` is given.

    Args:
        request: Pytest request object.

    Yields:
        A Jubilant object bound to the model.
    """
    if request.config.getoption("--no-deploy"):
        juju = jubilant.Juju(model=request.config.getoption("--model"))
        logger.info("--no-deploy: using the existing model '%s'", juju.model)
        yield _prepare(juju)
        _collect_juju_logs_if_failed(request, juju)
        return

    keep = request.config.getoption("--keep-models")
    with jubilant.temp_model(keep=keep) as juju:
        yield _prepare(juju)
        _collect_juju_logs_if_failed(request, juju)


@pytest.fixture(scope="function")
def bare_model(request: FixtureRequest) -> jubilant.Juju:
    """Give one scenario an empty model of its own.

    Yields:
        A Jubilant object bound to an empty temporary model.
    """
    yield from _model_for(request)


@pytest.fixture(scope="module")
def model(request: FixtureRequest) -> jubilant.Juju:
    """Give a scenario module one model to build its world in.

    Yields:
        A Jubilant object bound to an empty temporary model.
    """
    yield from _model_for(request)


@pytest.fixture(scope="module")
def superset_deployment(
    request: FixtureRequest,
    model: jubilant.Juju,
    charm: Path,
    charm_image: str,
) -> jubilant.Juju:
    """A complete Superset deployment on its dependencies, active.

    The UI, worker and beat scheduler run the charm's own defaults.

    Args:
        request: Pytest request object.
        model: The module's model.
        charm: Path to the packed charm.
        charm_image: The workload OCI image reference.

    Returns:
        The model, with the deployment active in it.
    """
    logger.info("Deploying a complete Superset deployment")
    return steps.adopt_or_build(
        request, model, steps.deploy_superset, charm, charm_image
    )


@pytest.fixture(scope="module")
def superset_deployment_with_ingress(
    request: FixtureRequest, superset_deployment: jubilant.Juju
) -> jubilant.Juju:
    """A Superset deployment whose UI is served through Traefik.

    Args:
        request: Pytest request object.
        superset_deployment: The active deployment.

    Returns:
        The model, with the UI behind an ingress.
    """
    logger.info("Putting the UI behind Traefik")
    return steps.adopt_or_build(
        request, superset_deployment, steps.deploy_traefik
    )


@pytest.fixture(autouse=True)
def log_scenario(request: FixtureRequest):
    """Log the title of the scenario about to run.

    Args:
        request: Pytest request object.
    """
    doc = (request.node.function.__doc__ or "").strip()
    if doc:
        logger.info("%s", doc.splitlines()[0])
