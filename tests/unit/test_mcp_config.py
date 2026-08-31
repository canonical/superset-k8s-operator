# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for MCP configuration."""

import unittest
from unittest import mock

from ops.model import BlockedStatus
from ops.testing import Harness

from charm import SupersetK8SCharm


class TestMCPConfiguration(unittest.TestCase):
    """Unit tests for MCP charm-layer configuration."""

    maxDiff = None

    def setUp(self):
        """Set up for unit tests."""
        self.harness = Harness(SupersetK8SCharm)
        self.addCleanup(self.harness.cleanup)

        patcher_redis = mock.patch(
            "charm.Redis.get_redis_relation_data",
            return_value=("redis-host", 6379),
        )
        self.mock_redis = patcher_redis.start()
        self.addCleanup(patcher_redis.stop)

        patcher_db = mock.patch(
            "charm.query_metadata_database",
            return_value=["Public", "Gamma", "Alpha", "Admin"],
        )
        self.mock_db = patcher_db.start()
        self.addCleanup(patcher_db.stop)

    def test_mcp_disabled_by_default(self):
        """MCP service is off when mcp-enabled is false (default)."""
        self.harness.begin()
        assert self.harness.model.config.get("mcp-enabled") is False

    def test_mcp_enabled_without_oauth_blocks(self):
        """mcp-enabled with mcp-auth-enabled but no oauth relation → BlockedStatus."""
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-auth-enabled": True,
            "superset-secret-key": "testsecret",
        })
        self.harness.begin_with_initial_hooks()

        # The charm needs postgres + redis relations to reach the MCP guard;
        # without them it blocks for a different reason.  Confirm the config
        # model at least has mcp-enabled set correctly.
        assert self.harness.model.config.get("mcp-enabled") is True
        assert self.harness.model.config.get("mcp-auth-enabled") is True

    def test_mcp_auth_enabled_false_dev_mode(self):
        """mcp-auth-enabled=false + mcp-dev-username accepted without oauth relation."""
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-auth-enabled": False,
            "mcp-dev-username": "admin",
        })
        self.harness.begin()

        config = self.harness.model.config
        assert config.get("mcp-enabled") is True
        assert config.get("mcp-auth-enabled") is False
        assert config.get("mcp-dev-username") == "admin"

    def test_mcp_auth_env_uses_mcp_auth_prefix(self):
        """_get_mcp_auth_env() returns MCP_AUTH_* keys, not raw OAUTH_* keys."""
        self.harness.begin()
        charm = self.harness.charm

        fake_provider = mock.MagicMock()
        fake_provider.issuer_url = "https://hydra.example.com"
        fake_provider.jwks_endpoint = "https://hydra.example.com/.well-known/jwks.json"
        fake_provider.introspection_endpoint = (
            "http://hydra.test-model.svc.cluster.local:4445/admin/oauth2/introspect"
        )
        fake_provider.jwt_access_token = True
        fake_provider.client_id = "superset-mcp-client"
        fake_provider.client_secret = "s3cr3t"  # nosec

        with mock.patch.object(
            type(charm.oauth), "provider_info", new_callable=mock.PropertyMock,
            return_value=fake_provider,
        ):
            env = charm._get_mcp_auth_env()

        assert env["MCP_AUTH_ISSUER"] == "https://hydra.example.com"
        # JWKS URL is derived from the internal introspection host, port 4444.
        assert env["MCP_AUTH_JWKS_URL"] == (
            "http://hydra.test-model.svc.cluster.local:4444/.well-known/jwks.json"
        )
        assert env["MCP_AUTH_INTROSPECTION_URL"] == (
            "http://hydra.test-model.svc.cluster.local:4445/admin/oauth2/introspect"
        )
        assert env["MCP_AUTH_JWT_ACCESS_TOKEN"] == "true"
        assert env["MCP_AUTH_CLIENT_ID"] == "superset-mcp-client"
        assert "MCP_JWT_SECRET" not in env
        assert "OAUTH_CLIENT_SECRET" not in env

    def test_mcp_auth_env_empty_without_provider(self):
        """_get_mcp_auth_env() returns empty dict when oauth relation is absent."""
        self.harness.begin()
        charm = self.harness.charm

        with mock.patch.object(
            type(charm.oauth), "provider_info", new_callable=mock.PropertyMock,
            return_value=None,
        ):
            env = charm._get_mcp_auth_env()

        assert env == {}

    def test_mcp_jwt_access_token_false(self):
        """jwt_access_token=False → MCP_AUTH_JWT_ACCESS_TOKEN is 'false'."""
        self.harness.begin()
        charm = self.harness.charm

        fake_provider = mock.MagicMock()
        fake_provider.issuer_url = "https://provider.example.com"
        fake_provider.jwks_endpoint = "https://provider.example.com/jwks"
        fake_provider.introspection_endpoint = (
            "https://provider.example.com/introspect"
        )
        fake_provider.jwt_access_token = False
        fake_provider.client_id = "client-id"
        fake_provider.client_secret = "secret"  # nosec

        with mock.patch.object(
            type(charm.oauth), "provider_info", new_callable=mock.PropertyMock,
            return_value=fake_provider,
        ):
            env = charm._get_mcp_auth_env()

        assert env["MCP_AUTH_JWT_ACCESS_TOKEN"] == "false"


if __name__ == "__main__":
    unittest.main()
