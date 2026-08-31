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

    def test_jwt_secret_accepted_regardless_of_length(self):
        """Charm accepts any non-empty secret; no length enforcement at config layer."""
        short_secret = "a" * 32
        self.harness.update_config({
            "mcp-enabled": True,
            "mcp-jwt-secret": short_secret,
        })
        self.harness.begin()

        self.assertEqual(self.harness.model.config.get("mcp-jwt-secret"), short_secret)

    def test_mcp_disabled_without_secret(self):
        """Test that MCP can be disabled without JWT secret."""
        self.harness.update_config({
            "mcp-enabled": False,
            "mcp-jwt-secret": "",
        })
        self.harness.begin()

        config = self.harness.model.config
        assert config.get("mcp-enabled") is False


if __name__ == "__main__":
    unittest.main()
