#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Feature: deploying Superset.

The charm is deployed as three applications and has requirements
that must be satisfied before it can serve requests.
"""

import logging
import re
import socket
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import jubilant
import pytest
import requests
import steps
from bdd import and_, given, then, when

logger = logging.getLogger(__name__)


def test_the_charm_names_the_relations_it_cannot_start_without(
    bare_model: jubilant.Juju, charm: Path, charm_image: str
):
    """Scenario: a deployment is missing both of its required relations.

    Given an empty model
    When the UI application is deployed with neither PostgreSQL nor Redis
    Then it blocks naming both of them
    """
    juju = bare_model

    with given("an empty model"):
        assert not juju.status().apps

    with when(
        "the UI application is deployed with neither PostgreSQL nor Redis"
    ):
        steps.deploy_superset_application(
            juju, charm, charm_image, "app-gunicorn"
        )

    with then("it blocks naming both of them"):
        steps.assert_blocked_with(
            juju,
            [steps.UI_NAME],
            "Required relations missing: PostgreSQL, Redis",
        )


def test_the_charm_blocks_without_a_signing_keys_secret(
    bare_model: jubilant.Juju, charm: Path, charm_image: str
):
    """Scenario: the signing keys are user-supplied and have no default.

    Given an empty model
    When the UI application is deployed without a signing keys secret
    Then it blocks naming the configuration it is missing
    """
    juju = bare_model

    with given("an empty model"):
        assert not juju.status().apps

    with when("the UI application is deployed without a signing keys secret"):
        steps.deploy_superset_application(
            juju,
            charm,
            charm_image,
            "app-gunicorn",
            with_signing_keys=False,
        )

    with then("it blocks naming the configuration it is missing"):
        steps.assert_blocked_with(
            juju,
            [steps.UI_NAME],
            "missing required config: signing-keys-secret-id",
        )


def test_the_charm_blocks_on_a_signing_keys_secret_missing_a_key(
    bare_model: jubilant.Juju, charm: Path, charm_image: str
):
    """Scenario: a signing keys secret carrying only one of its two keys.

    Given an empty model holding a secret with only `secret-key` in it
    When the UI application is deployed against that secret
    Then it blocks naming the key the secret does not carry
    """
    juju = bare_model

    with given("an empty model holding a secret with only `secret-key` in it"):
        uri = juju.add_secret(
            name="half-signing-keys",
            content={"secret-key": steps.SECRET_KEY},
        )
        secret_id = str(uri).rsplit(":", maxsplit=1)[-1]

    with when("the UI application is deployed against that secret"):
        steps.deploy_superset_application(
            juju,
            charm,
            charm_image,
            "app-gunicorn",
            config={"signing-keys-secret-id": secret_id},
            with_signing_keys=False,
        )
        juju.grant_secret("half-signing-keys", steps.UI_NAME)

    with then("it blocks naming the key the secret does not carry"):
        steps.assert_blocked_with(
            juju,
            [steps.UI_NAME],
            "improper schema. Missing: async-queries-jwt",
        )


def test_a_complete_deployment_becomes_active(
    superset_deployment: jubilant.Juju,
):
    """Scenario: the UI, the worker and the beat scheduler all come up.

    Given a complete Superset deployment with PostgreSQL and Redis
    Then every one of its three applications is active
    And the worker answers on the Celery broker
    """
    juju = superset_deployment

    with given("a complete Superset deployment with PostgreSQL and Redis"):
        pass

    with then("every one of its three applications is active"):
        steps.assert_active(juju, steps.SUPERSET_APPS)

    with and_("the worker answers on the Celery broker"):
        workers = steps.wait_for_celery_workers(juju, 1)
        logger.info("Celery workers on the broker: %s", list(workers))


def test_the_ui_serves_its_login_page(superset_deployment: jubilant.Juju):
    """Scenario: the workload the UI application runs answers requests.

    Given a complete Superset deployment
    When the UI unit's address is requested
    Then Superset answers it
    """
    juju = superset_deployment

    with given("a complete Superset deployment"):
        steps.assert_active(juju, [steps.UI_NAME])

    with when("the UI unit's address is requested"):
        url = steps.get_unit_url(juju, steps.UI_NAME)
        response = steps.request_until(None, "GET", url)

    with then("Superset answers it"):
        assert (
            response.status_code == 200
        ), f"{url} returned {response.status_code}: {response.text[:200]}"


def test_the_generated_admin_password_logs_in(
    superset_deployment: jubilant.Juju,
):
    """Scenario: the charm generates the admin account's password.

    Given a complete Superset deployment
    When the get-admin-password action is run on the UI
    Then the password it returns authenticates against the Superset API
    """
    juju = superset_deployment

    with given("a complete Superset deployment"):
        steps.assert_active(juju, [steps.UI_NAME])

    with when("the get-admin-password action is run on the UI"):
        password = steps.get_admin_password(juju)

    with then(
        "the password it returns authenticates against the Superset API"
    ):
        assert password, "the action returned an empty password"
        # `api_session` logs in with exactly this password, so reaching an
        # endpoint that requires the bearer token is what proves it worked.
        session, url = steps.api_session(juju)
        response = session.get(f"{url}/api/v1/chart/", timeout=30)
        assert response.status_code == 200, response.text[:200]


def test_a_browser_login_gets_a_secure_session_cookie(
    superset_deployment_with_ingress: jubilant.Juju,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Scenario: a browser authenticates through the HTTPS ingress.

    Given a Superset deployment behind a trusted HTTPS ingress
    When the admin logs in using the login form and normal cookie handling
    Then the session cookie is Secure, HttpOnly and SameSite=Lax
    And the session alone authenticates a subsequent request
    """
    juju = superset_deployment_with_ingress

    with given("a Superset deployment behind a trusted HTTPS ingress"):
        url = steps.proxied_url(juju).rstrip("/")
        assert urlparse(url).scheme == "https"
        return_code, ca = steps.read_workload_file(
            juju, f"{steps.UI_NAME}/0", steps.CA_CERT_PATH
        )
        assert return_code == 0, "the ingress CA is not installed"
        ca_bundle = tmp_path / "ingress-ca.pem"
        ca_bundle.write_text(ca)

        hostname = urlparse(url).hostname
        address = urlparse(
            steps.get_unit_url(juju, steps.TRAEFIK_NAME)
        ).hostname
        getaddrinfo = socket.getaddrinfo

        def resolve_ingress(host, *args, **kwargs):
            """Resolve the test-only hostname without changing TLS identity.

            Args:
                host: Hostname to resolve.
                args: Positional arguments forwarded to getaddrinfo.
                kwargs: Keyword arguments forwarded to getaddrinfo.

            Returns:
                Socket addresses for the requested host.
            """
            return getaddrinfo(
                address if host == hostname else host, *args, **kwargs
            )

        # The .test ingress domain has no public DNS record. Keep the URL
        # hostname for SNI, certificate validation and cookie scoping.
        monkeypatch.setattr(socket, "getaddrinfo", resolve_ingress)

    with requests.Session() as session:
        session.trust_env = False
        session.verify = str(ca_bundle)
        login_url = f"{url}/login/"
        me_url = f"{url}/api/v1/me/"

        with when(
            "the admin logs in using the login form and normal cookie handling"
        ):
            login_page = steps.request_until(session, "GET", login_url)
            assert login_page.status_code == 200
            csrf = re.search(
                r'name="csrf_token"[^>]*value="([^"]+)"', login_page.text
            )
            assert csrf is not None, "the login form has no CSRF token"
            anonymous = session.get(me_url, timeout=30, allow_redirects=False)
            assert anonymous.status_code == 401

            login = session.post(
                login_url,
                data={
                    "username": "admin",
                    "password": steps.get_admin_password(juju),
                    "csrf_token": unescape(csrf.group(1)),
                },
                headers={"Referer": login_url},
                timeout=30,
                allow_redirects=False,
            )
            assert login.status_code == 302

        with then("the session cookie is Secure, HttpOnly and SameSite=Lax"):
            cookies = [
                cookie
                for cookie in session.cookies
                if cookie.name == "session"
            ]
            assert len(cookies) == 1
            assert cookies[0].secure
            assert cookies[0].has_nonstandard_attr("HttpOnly")
            assert cookies[0].get_nonstandard_attr("SameSite") == "Lax"

        with and_("the session alone authenticates a subsequent request"):
            authenticated = session.get(
                me_url, timeout=30, allow_redirects=False
            )
            assert authenticated.status_code == 200
            assert authenticated.json()["result"]["username"] == "admin"


@pytest.mark.parametrize("app", [steps.WORKER_NAME, steps.BEAT_NAME])
def test_get_admin_password_fails_off_the_ui(
    superset_deployment: jubilant.Juju, app: str
):
    """Scenario: only the UI application creates the admin account.

    Given a complete Superset deployment
    When the get-admin-password action is run on an app that is not the UI
    Then the action fails rather than returning a password
    """
    juju = superset_deployment

    with given("a complete Superset deployment"):
        steps.assert_active(juju, [app])

    with then("the action fails rather than returning a password"):
        with pytest.raises(jubilant.TaskError):
            juju.run(f"{app}/0", "get-admin-password", wait=5 * 60)


def test_the_ingress_routes_to_the_ui(
    superset_deployment_with_ingress: jubilant.Juju,
):
    """Scenario: the URL the ingress publishes reaches Superset.

    Given a Superset deployment whose UI is behind Traefik
    When the URL Traefik publishes for the UI is requested
    Then Superset answers it
    """
    juju = superset_deployment_with_ingress

    with given("a Superset deployment whose UI is behind Traefik"):
        steps.assert_active(juju, [steps.TRAEFIK_NAME, steps.UI_NAME])

    with when("the URL Traefik publishes for the UI is requested"):
        logger.info("Ingress URL: %s", steps.proxied_url(juju))

    with then("Superset answers it"):
        steps.assert_ingress_serves(juju)
