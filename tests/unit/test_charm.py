# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing


"""Charm unit tests."""

# pylint:disable=protected-access

import json
import logging
from unittest import TestCase, mock

from ops.model import (
    ActiveStatus,
    BlockedStatus,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import CheckStatus
from ops.testing import Harness

from charm import SupersetK8SCharm
from state import State

SERVER_PORT = "8088"
logger = logging.getLogger(__name__)


class TestCharm(TestCase):
    """Unit tests.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def setUp(self):
        """Set up for the unit tests."""
        self.harness = Harness(SupersetK8SCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.set_can_connect("superset", True)
        self.harness.set_leader(True)
        self.harness.set_model_name("superset-model")
        self.harness.add_network("10.0.0.10", endpoint="peer")
        self.harness.begin()
        logging.info("setup complete")

    def test_initial_plan(self):
        """The initial pebble plan is empty."""
        harness = self.harness
        initial_plan = harness.get_container_pebble_plan("superset").to_dict()
        self.assertEqual(initial_plan, {})

    def test_waiting_on_peer_relation_not_ready(self):
        """The charm is blocked without a peer relation."""
        harness = self.harness

        # Simulate pebble readiness.
        container = harness.model.unit.get_container("superset")
        harness.charm.on.superset_pebble_ready.emit(container)

        # No plans are set yet.
        got_plan = harness.get_container_pebble_plan("superset").to_dict()
        self.assertEqual(got_plan, {})

        # The BlockStatus is set with a message.
        self.assertEqual(
            harness.model.unit.status,
            WaitingStatus("Waiting for peer relation."),
        )

    def test_ready(self):
        """The pebble plan is correctly generated when the charm is ready."""
        harness = self.harness
        simulate_lifecycle(harness)

        # The plan is generated after pebble is ready.
        want_plan = {
            "services": {
                "superset": {
                    "override": "replace",
                    "summary": "superset server",
                    "command": "/app/k8s/k8s-bootstrap.sh",
                    "startup": "enabled",
                    "environment": {
                        "SUPERSET_SECRET_KEY": "example-pass",
                        "ADMIN_USER": "unique-user",
                        "ADMIN_PASSWORD": "admin",
                        "CHARM_FUNCTION": "app-gunicorn",
                        "SQL_ALCHEMY_URI": "postgresql://postgres_user:admin@myhost:5432/superset",
                        "REDIS_HOST": "redis-host",
                        "REDIS_PORT": 6379,
                        "ALERTS_ATTACH_REPORTS": True,
                        "DASHBOARD_CROSS_FILTERS": True,
                        "DASHBOARD_RBAC": True,
                        "EMBEDDABLE_CHARTS": True,
                        "SCHEDULED_QUERIES": True,
                        "ESTIMATE_QUERY_COST": True,
                        "ENABLE_TEMPLATE_PROCESSING": True,
                        "ALERT_REPORTS": True,
                        "SQLALCHEMY_POOL_SIZE": 5,
                        "SQLALCHEMY_POOL_TIMEOUT": 300,
                        "SQLALCHEMY_MAX_OVERFLOW": 10,
                    },
                    "on-check-failure": {"up": "ignore"},
                }
            },
        }
        got_plan = harness.get_container_pebble_plan("superset").to_dict()
        got_plan["services"]["superset"]["environment"][
            "SUPERSET_SECRET_KEY"
        ] = "example-pass"  # nosec
        got_plan["services"]["superset"]["environment"][
            "ADMIN_USER"
        ] = "unique-user"
        self.assertEqual(got_plan, want_plan)

        # The service was started.
        service = harness.model.unit.get_container("superset").get_service(
            "superset"
        )
        self.assertTrue(service.is_running())

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_invalid_config_value(self):
        """The charm blocks if an invalid config value is provided."""
        harness = self.harness
        simulate_lifecycle(harness)

        # Update the config with an invalid value.
        self.harness.update_config({"charm-function": "web"})

        # The change is not applied to the plan.
        want_charm_function = "app-gunicorn"
        got_log_level = harness.get_container_pebble_plan(
            "superset"
        ).to_dict()["services"]["superset"]["environment"]["CHARM_FUNCTION"]
        self.assertEqual(got_log_level, want_charm_function)

        # The BlockStatus is set with a message.
        self.assertEqual(
            harness.model.unit.status,
            BlockedStatus("config: invalid charm function 'web'"),
        )

    def test_config_changed(self):
        """The pebble plan changes according to config changes."""
        harness = self.harness
        simulate_lifecycle(harness)

        # Update the config.
        self.harness.update_config({"admin-password": "secure-pass"})

        # The new plan reflects the change.
        want_plan = {
            "services": {
                "superset": {
                    "override": "replace",
                    "summary": "superset server",
                    "command": "/app/k8s/k8s-bootstrap.sh",
                    "startup": "enabled",
                    "environment": {
                        "SUPERSET_SECRET_KEY": "example-pass",
                        "ADMIN_PASSWORD": "secure-pass",
                        "ADMIN_USER": "unique-user",
                        "CHARM_FUNCTION": "app-gunicorn",
                        "SQL_ALCHEMY_URI": "postgresql://postgres_user:admin@myhost:5432/superset",
                        "REDIS_HOST": "redis-host",
                        "REDIS_PORT": 6379,
                        "ALERTS_ATTACH_REPORTS": True,
                        "DASHBOARD_CROSS_FILTERS": True,
                        "DASHBOARD_RBAC": True,
                        "EMBEDDABLE_CHARTS": True,
                        "SCHEDULED_QUERIES": True,
                        "ESTIMATE_QUERY_COST": True,
                        "ENABLE_TEMPLATE_PROCESSING": True,
                        "ALERT_REPORTS": True,
                        "SQLALCHEMY_POOL_SIZE": 5,
                        "SQLALCHEMY_POOL_TIMEOUT": 300,
                        "SQLALCHEMY_MAX_OVERFLOW": 10,
                    },
                    "on-check-failure": {"up": "ignore"},
                },
            },
        }
        got_plan = harness.get_container_pebble_plan("superset").to_dict()
        got_plan["services"]["superset"]["environment"][
            "SUPERSET_SECRET_KEY"
        ] = "example-pass"  # nosec
        got_plan["services"]["superset"]["environment"][
            "ADMIN_USER"
        ] = "unique-user"
        self.assertEqual(got_plan, want_plan)

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_ingress(self):
        """The charm relates correctly to the nginx ingress charm."""
        harness = self.harness

        simulate_lifecycle(harness)

        nginx_route_relation_id = harness.add_relation(
            "nginx-route", "ingress"
        )
        harness.charm._require_nginx_route()

        assert harness.get_relation_data(
            nginx_route_relation_id, harness.charm.app
        ) == {
            "service-namespace": harness.charm.model.name,
            "service-hostname": harness.charm.app.name,
            "service-name": harness.charm.app.name,
            "service-port": SERVER_PORT,
            "backend-protocol": "HTTPS",
            "tls-secret-name": "superset-tls",
        }

    def test_update_status_up(self):
        """The charm updates the unit status to active based on UP status."""
        harness = self.harness

        simulate_lifecycle(harness)

        container = harness.model.unit.get_container("superset")
        container.get_check = mock.Mock(status="up")
        container.get_check.return_value.status = CheckStatus.UP
        harness.charm.on.update_status.emit()

        self.assertEqual(
            harness.model.unit.status, ActiveStatus("Status check: UP")
        )

    def test_update_status_down(self):
        """The charm updates the unit status to maintenance based on DOWN status."""
        harness = self.harness

        simulate_lifecycle(harness)

        container = harness.model.unit.get_container("superset")
        container.get_check = mock.Mock(status="up")
        container.get_check.return_value.status = CheckStatus.DOWN
        harness.charm.on.update_status.emit()

        self.assertEqual(
            harness.model.unit.status, MaintenanceStatus("Status check: DOWN")
        )

    def test_beat_deployment(self):
        """The pebble plan reflects the beat function."""
        harness = self.harness
        self.harness.update_config({"charm-function": "beat"})

        simulate_lifecycle(harness)

        # The plan reflects the function.
        want_function = "beat"
        got_function = harness.get_container_pebble_plan("superset").to_dict()
        got_function = got_function["services"]["superset"]["environment"][
            "CHARM_FUNCTION"
        ]
        self.assertEqual(got_function, want_function)

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )

    def test_worker_deployment(self):
        """The pebble plan reflects the worker function."""
        harness = self.harness
        self.harness.update_config({"charm-function": "worker"})

        simulate_lifecycle(harness)

        # The plan reflects the function.
        want_function = "worker"
        got_function = harness.get_container_pebble_plan("superset").to_dict()
        got_function = got_function["services"]["superset"]["environment"][
            "CHARM_FUNCTION"
        ]
        self.assertEqual(got_function, want_function)

        # The MaintenanceStatus is set with replan message.
        self.assertEqual(
            harness.model.unit.status,
            MaintenanceStatus("replanning application"),
        )


@mock.patch("charm.Redis._get_redis_relation_data")
def simulate_lifecycle(harness, _get_redis_relation_data):
    """Simulate a healthy charm life-cycle.

    Args:
        harness: ops.testing.Harness object used to simulate charm lifecycle.
    """
    # Simulate peer relation readiness.
    harness.add_relation("peer", "superset")

    # Simulate pebble readiness.
    container = harness.model.unit.get_container("superset")
    harness.charm.on.superset_pebble_ready.emit(container)

    # Simulate redis readiness.
    _get_redis_relation_data.return_value = ("redis-host", 6379, True)
    rel_id = harness.add_relation("redis", "redis-k8s")
    harness.add_relation_unit(rel_id, "redis-k8s/0")

    # Simulate database readiness.
    event = make_database_changed_event()
    harness.charm.database._on_database_changed(event)


def make_database_changed_event():
    """Create and return a mock database changed event.

        The event is generated by the relation with postgresql_db

    Returns:
        Event dict.
    """
    return type(
        "Event",
        (),
        {
            "endpoints": "myhost:5432",
            "username": "postgres_user",
            "password": "admin",
            "database": "superset",
            "relation": type("Relation", (), {"name": "postgresql_db"}),
        },
    )


class TestState(TestCase):
    """Unit tests for state.

    Attrs:
        maxDiff: Specifies max difference shown by failed tests.
    """

    maxDiff = None

    def test_get(self):
        """It is possible to retrieve attributes from the state."""
        state = make_state({"foo": json.dumps("bar")})
        self.assertEqual(state.foo, "bar")
        self.assertIsNone(state.bad)

    def test_set(self):
        """It is possible to set attributes in the state."""
        data = {"foo": json.dumps("bar")}
        state = make_state(data)
        state.foo = 42
        state.list = [1, 2, 3]
        self.assertEqual(state.foo, 42)
        self.assertEqual(state.list, [1, 2, 3])
        self.assertEqual(data, {"foo": "42", "list": "[1, 2, 3]"})

    def test_del(self):
        """It is possible to unset attributes in the state."""
        data = {"foo": json.dumps("bar"), "answer": json.dumps(42)}
        state = make_state(data)
        del state.foo
        self.assertIsNone(state.foo)
        self.assertEqual(data, {"answer": "42"})
        # Deleting a name that is not set does not error.
        del state.foo

    def test_is_ready(self):
        """The state is not ready when it is not possible to get relations."""
        state = make_state({})
        self.assertTrue(state.is_ready())

        state = State("myapp", lambda: None)
        self.assertFalse(state.is_ready())


def make_state(data):
    """Create state object.

    Args:
        data: Data to be included in state.

    Returns:
        State object with data.
    """
    app = "myapp"
    rel = type("Rel", (), {"data": {app: data}})()
    return State(app, lambda: rel)
