# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Library for the trino_catalog relation.

This library provides the TrinoCatalogProvider and TrinoCatalogRequirer classes that
handle the provider and the requirer sides of the trino_catalog interface.
"""

import json
import logging
from typing import List, Optional

from ops.charm import CharmBase
from ops.framework import Object
from ops.model import ModelError, SecretNotFoundError

# The unique Charmhub library identifier, never change it
LIBID = "8855efa80c9a407991dafe157a762305"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2



logger = logging.getLogger(__name__)


class TrinoCatalog:
    """Represents a Trino catalog."""

    def __init__(self, name: str, connector: str = "", description: str = ""):
        """Initialize a TrinoCatalog.

        Args:
            name: Catalog name (e.g., "marketing", "sales")
            connector: Optional connector type (e.g., "postgresql", "mysql", "bigquery")
            description: Optional description of the catalog
        """
        self.name = name
        self.connector = connector
        self.description = description

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary with name, connector, and description keys
        """
        return {
            "name": self.name,
            "connector": self.connector,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrinoCatalog":
        """Create TrinoCatalog from dictionary.

        Args:
            data: Dictionary with name, optional connector, and optional description

        Returns:
            TrinoCatalog instance
        """
        return cls(
            name=data["name"],
            connector=data.get("connector", ""),
            description=data.get("description", ""),
        )

    def __repr__(self) -> str:
        """String representation for debugging.

        Returns:
            String representation of the TrinoCatalog object.
        """
        return f"TrinoCatalog(name={self.name}, connector={self.connector}, description={self.description})"

    def __eq__(self, other) -> bool:
        """Compare two catalogs for equality.

        Args:
            other: Object to compare with.

        Returns:
            True if catalogs are equal, False otherwise.
        """
        if not isinstance(other, TrinoCatalog):
            return False
        return (
            self.name == other.name
            and self.connector == other.connector
            and self.description == other.description
        )


class TrinoCatalogProvider(Object):
    """Provider side of the trino_catalog relation.

    This library handles the relation lifecycle and data updates.
    The charm is responsible for providing the actual data (url, catalogs, secret).

    Note: The credentials secret is model-owned and must be manually granted
    to each requirer application using: juju grant-secret <secret> <requirer-app>
    """

    def __init__(self, charm: CharmBase, relation_name: str = "trino-catalog"):
        """Initialize the TrinoCatalogProvider.

        Args:
            charm: The charm instance.
            relation_name: Name of the relation.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def update_relation_data(
        self,
        relation,
        trino_url: str,
        trino_catalogs: List[TrinoCatalog],
        trino_credentials_secret_id: str,
    ) -> bool:
        """Update relation data for a specific relation.

        Args:
            relation: The relation to update
            trino_url: Trino URL (e.g., "trino.example.com:443")
            trino_catalogs: List of TrinoCatalog objects
            trino_credentials_secret_id: Juju secret ID containing Trino users

        Returns:
            True if successful, False otherwise
        """
        logger.info("Updating trino-catalog relation %s", relation)

        if not trino_url:
            logger.debug("Trino URL not provided, skipping relation update")
            return False

        if not trino_credentials_secret_id:
            logger.debug(
                "Trino credentials secret ID not provided, skipping relation update"
            )
            return False

        # Get current values from databag
        current_data = relation.data[self.charm.app]
        current_url = current_data.get("trino_url")
        current_catalogs_str = current_data.get("trino_catalogs")
        current_secret_id = current_data.get("trino_credentials_secret_id")

        # Get new values
        new_url = trino_url
        try:
            new_catalogs_str = json.dumps(
                sorted(
                    [c.to_dict() for c in trino_catalogs],
                    key=lambda x: x["name"],
                )
            )
        except (TypeError, KeyError) as e:
            logger.error(
                "Failed to serialize catalogs for relation %s: %s",
                relation.id,
                str(e),
            )
            return False

        new_secret_id = trino_credentials_secret_id

        # Detect changes
        url_changed = current_url != new_url
        catalogs_changed = current_catalogs_str != new_catalogs_str
        secret_id_changed = current_secret_id != new_secret_id

        # If nothing changed, skip update
        if not (url_changed or catalogs_changed or secret_id_changed):
            logger.debug(
                "No changes for relation %s, skipping update", relation.id
            )
            return True

        # Update relation databag
        relation.data[self.charm.app].update(
            {
                "trino_url": new_url,
                "trino_catalogs": new_catalogs_str,
                "trino_credentials_secret_id": new_secret_id,
            }
        )

        # Log what changed
        changes = []
        if url_changed:
            changes.append("URL")
        if catalogs_changed:
            changes.append("catalogs")
        if secret_id_changed:
            changes.append("credentials")

        logger.info(
            "Updated trino-catalog relation %s: %s changed",
            relation.id,
            ", ".join(changes),
        )
        return True

    def update_all_relations(
        self,
        trino_url: str,
        trino_catalogs: List[TrinoCatalog],
        trino_credentials_secret_id: str,
    ) -> None:
        """Update all trino-catalog relations with the provided data.

        Args:
            trino_url: Trino URL
            trino_catalogs: List of TrinoCatalog objects
            trino_credentials_secret_id: Juju secret ID containing Trino users
        """
        for relation in self.charm.model.relations.get(self.relation_name, []):
            self.update_relation_data(
                relation=relation,
                trino_url=trino_url,
                trino_catalogs=trino_catalogs,
                trino_credentials_secret_id=trino_credentials_secret_id,
            )


class TrinoCatalogRequirer(Object):
    """Requirer side of the trino_catalog relation."""

    def __init__(self, charm: CharmBase, relation_name: str = "trino-catalog"):
        """Initialize the TrinoCatalogRequirer.

        Args:
            charm: The charm instance.
            relation_name: Name of the relation.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name

    def get_trino_info(self) -> Optional[dict]:
        """Get current Trino connection information.

        Returns:
            Dictionary with trino_url, trino_catalogs (List[TrinoCatalog]),
            and trino_credentials_secret_id, or None if not available.
        """
        relations = self.charm.model.relations.get(self.relation_name, [])
        if not relations:
            return None

        relation = relations[0]
        if not relation.app:
            return None

        relation_data = relation.data[relation.app]

        trino_url = relation_data.get("trino_url")
        trino_catalogs_str = relation_data.get("trino_catalogs")
        trino_credentials_secret_id = relation_data.get(
            "trino_credentials_secret_id"
        )

        if not all(
            [trino_url, trino_catalogs_str, trino_credentials_secret_id]
        ):
            return None

        try:
            catalogs_list = json.loads(trino_catalogs_str)
            trino_catalogs = [TrinoCatalog.from_dict(c) for c in catalogs_list]
        except (json.JSONDecodeError, KeyError):
            return None

        return {
            "trino_url": trino_url,
            "trino_catalogs": trino_catalogs,
            "trino_credentials_secret_id": trino_credentials_secret_id,
        }

    def get_credentials(self) -> Optional[tuple]:
        """Get Trino credentials to use from the secret.

        Note: The requirer application must be granted access to the secret
        using: juju grant-secret <secret> <requirer-app>. The secret must
        contain a user matching the format "app-<requirer-charm-name>".

        Returns:
            Tuple of (username, password) or None if not available.

        Raises:
            SecretNotFoundError: If the secret does not exist.
            ModelError: If permission is denied to access the secret.
        """
        required_username = f"app-{self.charm.meta.name}"

        trino_info = self.get_trino_info()
        if not trino_info:
            return None

        try:
            secret = self.charm.model.get_secret(
                id=trino_info["trino_credentials_secret_id"]
            )
            credentials = secret.get_content(refresh=True)
        except SecretNotFoundError:
            logger.error(
                "Secret '%s' not found.",
                trino_info["trino_credentials_secret_id"],
            )
            raise
        except ModelError as e:
            logger.error(
                "Permission denied accessing secret '%s': %s. Run juju grant-secret",
                trino_info["trino_credentials_secret_id"],
                str(e),
            )
            raise

        users_data = credentials.get("users", "")

        # Parse "username: password" format
        users = {}
        for line in users_data.strip().split("\n"):
            if ":" in line:
                username, password = line.split(":", 1)
                users[username.strip()] = password.strip()

        # Search for the required user
        if required_username not in users:
            logger.error(
                "User '%s' not found in Trino user credentials.",
                required_username,
            )
            return None

        return (required_username, users[required_username])
