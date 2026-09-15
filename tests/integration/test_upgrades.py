#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: refreshing a deployed charm to the new one being built.

Merging publishes to `latest/edge`, and the revision is promoted from there.
Every published release an operator can be running has to come out of that
refresh as active and with its content intact.
"""

import logging
from pathlib import Path

import jubilant
import pytest
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)

# The published releases a deployment can be refreshed from to
# test both major and minor version upgrades.
BASELINES = ["5/stable", "6/stable"]

# Baselines published before the signing keys secret replaced
# `superset-secret-key`. They take the key as plain configuration, which the
# charm being built no longer declares. Drop a channel from here once a
# revision carrying the secret is promoted into it, and delete the constant
# and the branch that reads it once it is empty.
LEGACY_SECRET_KEY_BASELINES = frozenset({"5/stable", "6/stable"})


def configure_signing_keys(juju: jubilant.Juju, app: str) -> None:
    """Give an application the signing keys secret this charm requires.

    Args:
        juju: Jubilant object.
        app: Application name.
    """
    secret_id = steps.add_signing_keys_secret(juju)
    juju.grant_secret(steps.SIGNING_KEYS_SECRET_NAME, app)
    juju.config(app, {"signing-keys-secret-id": secret_id})


def deploy_baseline(juju: jubilant.Juju, channel: str) -> None:
    """Deploy a published release on its dependencies.

    Args:
        juju: Jubilant object.
        channel: The channel to deploy from.
    """
    logger.info("Deploying '%s' from channel '%s'", steps.CHARM_NAME, channel)
    legacy = channel in LEGACY_SECRET_KEY_BASELINES

    steps.deploy_dependencies(juju)
    config: dict = {"load-examples": True}
    if legacy:
        config["superset-secret-key"] = steps.SECRET_KEY

    juju.deploy(
        steps.CHARM_NAME,
        app=steps.CHARM_NAME,
        channel=channel,
        config=config,
    )
    steps.integrate_dependencies(juju, steps.CHARM_NAME)
    if not legacy:
        configure_signing_keys(juju, steps.CHARM_NAME)

    steps.wait_for_active(
        juju, [steps.CHARM_NAME], timeout=steps.DEPLOY_TIMEOUT
    )


@pytest.fixture(scope="module")
def a_published_deployment(request: pytest.FixtureRequest):
    """Deploy a baseline in a model of its own.

    Args:
        request: Pytest request object, carrying the channel to deploy.

    Yields:
        A tuple of the Jubilant object and the channel it deployed from.
    """
    # A model kept from an earlier run already holds the refreshed charm.
    if request.config.getoption("--no-deploy"):
        pytest.skip("a kept model no longer holds a published release")

    channel = request.param
    keep = request.config.getoption("--keep-models")
    with jubilant.temp_model(keep=keep) as juju:
        juju.wait_timeout = steps.DEPLOY_TIMEOUT
        deploy_baseline(juju, channel)

        yield juju, channel

        if request.session.testsfailed:
            logger.info("Collecting Juju logs from model '%s'", juju.model)
            logger.info("%s", juju.debug_log(limit=500))


@pytest.mark.parametrize("a_published_deployment", BASELINES, indirect=True)
def test_a_published_deployment_survives_the_refresh(
    a_published_deployment, charm: Path, charm_image: str
):
    """Scenario: a published deployment is refreshed onto this charm.

    Given a deployment of a published release serving its example content
    When it is refreshed onto the charm being built
    Then it is active and serving
    And the example content it held is still there
    """
    juju, channel = a_published_deployment
    app = steps.CHARM_NAME

    with given("a deployment of a published release serving its content"):
        logger.info("Baseline channel: %s", channel)
        steps.assert_ui_serves(juju, app)

    with when("it is refreshed onto the charm being built"):
        # The UI loads the examples on every start, so leaving this on would
        # put back any charts the refresh lost.
        juju.config(app, {"load-examples": False})
        steps.refresh_to_local(juju, app, charm, charm_image)
        configure_signing_keys(juju, app)

    with then("it is active and serving"):
        steps.wait_for_active(juju, [app], timeout=steps.DEPLOY_TIMEOUT)
        steps.assert_ui_serves(juju, app)

    with and_("the example content it held is still there"):
        session, url = steps.api_session(juju, app)
        charts = steps.chart_names(session, url)
        assert charts, "the refreshed deployment holds no charts"
        logger.info("Charts surviving the refresh: %d", len(charts))
