# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Unit tests for MCP configuration validation."""

import unittest
from unittest import mock

from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import SupersetK8SCharm


class TestMCPConfiguration(unittest.TestCase):
    """Unit tests for MCP configuration."""

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

    def test_mcp_enabled_config(self):
        """Test charm config validation accepts mcp-enabled=true."""
        self.harness.update_config({"mcp-enabled": True})
        self.harness.begin()

        config = self.harness.model.config
        assert config.get("mcp-enabled") is True

    def test_mcp_with_jwt_secret(self):
        """Test MCP is enabled with JWT secret configured."""
        jwt_secret = "a" * 64
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-jwt-secret": jwt_secret,
        })
        self.harness.begin()

        config = self.harness.model.config
        assert config.get("mcp-enabled") is True
        assert config.get("mcp-jwt-secret") == jwt_secret

    def test_jwt_secret_format_accepted(self):
        """Test JWT secret in hex format is accepted by charm config."""
        jwt_secret_hex = "a" * 64
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-jwt-secret": jwt_secret_hex,
        })
        self.harness.begin()

        jwt_secret = self.harness.model.config.get("mcp-jwt-secret")
        self.assertEqual(jwt_secret, jwt_secret_hex)

    def test_mcp_disabled_without_secret(self):
        """Test that MCP can be disabled without JWT secret."""
        self.harness.update_config({
            "mcp-enabled": False,
            "mcp-jwt-secret": "",
        })
        self.harness.begin()

        config = self.harness.model.config
        assert config.get("mcp-enabled") is False

    def test_jwt_secret_length_recommendation(self):
        """Test JWT secret has recommended length (32 bytes = 64 hex chars)."""
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-jwt-secret": "a" * 64,
        })
        self.harness.begin()

        jwt_secret = self.harness.model.config.get("mcp-jwt-secret")
        hex_bytes = len(bytes.fromhex(jwt_secret))
        self.assertGreaterEqual(hex_bytes, 32, "JWT secret should be at least 32 bytes")


if __name__ == "__main__":
    unittest.main()
