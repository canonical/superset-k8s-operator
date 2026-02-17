# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset REST API client for managing database connections and permissions."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

import jwt
import requests

logger = logging.getLogger(__name__)

# Pagination defaults
MAX_PAGE_SIZE = 100
MAX_PAGES = 50

# HTTP request timeout in seconds
DEFAULT_REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class TrinoConnection:
    """Represents a Trino database connection in Superset.

    Attributes:
        id: Superset database connection ID.
        database_name: Name of the database connection in Superset.
        sqlalchemy_uri: Full SQLAlchemy URI for the connection.
        catalog: Trino catalog name extracted from the URI.
    """

    id: int
    database_name: str
    sqlalchemy_uri: str
    catalog: str


class SupersetApiError(Exception):
    """Raised when a Superset API call fails."""

    def __init__(self, message: str, status_code: int | None = None):
        """Initialize SupersetApiError.

        Args:
            message: Error message.
            status_code: HTTP status code, if applicable.
        """
        super().__init__(message)
        self.status_code = status_code


class SupersetApiClient:
    """Client for the Superset REST API.

    Handles authentication and provides methods for managing
    Trino database connections and role permissions.
    """

    def __init__(
        self,
        admin_username: str,
        admin_password: str,
        base_url: str = "http://localhost:8088",
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ):
        """Initialize the Superset API client.

        Args:
            admin_username: Superset admin username.
            admin_password: Superset admin password.
            base_url: Superset base URL (default: http://localhost:8088).
            timeout: Request timeout in seconds (default: 30).
        """
        self.base_url = base_url.rstrip("/")
        self._admin_username = admin_username
        self._admin_password = admin_password
        self._timeout = timeout
        self._session = requests.Session()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._csrf_token: str | None = None
        self._access_exp: datetime | None = None

    def _authenticate(self) -> None:
        """Authenticate with Superset and get tokens.

        Raises:
            SupersetApiError: If authentication fails.
        """
        login_url = f"{self.base_url}/api/v1/security/login"
        payload = {
            "username": self._admin_username,
            "password": self._admin_password,
            "provider": "db",
            "refresh": True,
        }

        try:
            response = self._session.post(
                login_url, json=payload, timeout=self._timeout
            )
            response.raise_for_status()

            data = response.json()
            self._access_token = data.get("access_token")
            self._refresh_token = data.get("refresh_token")

            if not self._access_token or not self._refresh_token:
                raise SupersetApiError("Access or refresh token not received")

            # Decode access token expiry
            try:
                payload_decoded = jwt.decode(
                    self._access_token, options={"verify_signature": False}
                )
                exp = payload_decoded.get("exp")
                if exp:
                    self._access_exp = datetime.fromtimestamp(
                        exp, timezone.utc
                    )
            except Exception as e:
                logger.warning("Failed to decode JWT expiry: %s", e)

            # Get CSRF token
            self._fetch_csrf_token()

            logger.info("Successfully authenticated with Superset")

        except requests.RequestException as e:
            logger.error("Authentication failed: %s", e)
            raise SupersetApiError(f"Authentication failed: {e}") from e

    def _fetch_csrf_token(self) -> None:
        """Fetch the CSRF token using the access token.

        Raises:
            SupersetApiError: If CSRF token cannot be obtained.
        """
        csrf_url = f"{self.base_url}/api/v1/security/csrf_token/"
        headers = {"Authorization": f"Bearer {self._access_token}"}

        try:
            response = self._session.get(
                csrf_url, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()

            self._csrf_token = response.json().get("result")
            if not self._csrf_token:
                raise SupersetApiError("CSRF token not received")

            logger.debug("Successfully obtained CSRF token")

        except requests.RequestException as e:
            raise SupersetApiError(f"Failed to obtain CSRF token: {e}") from e

    def _refresh_access_token(self) -> None:
        """Refresh the access token using the refresh token.

        Raises:
            SupersetApiError: If token refresh fails.
        """
        if not self._refresh_token:
            raise SupersetApiError("No refresh token available")

        refresh_url = f"{self.base_url}/api/v1/security/refresh"
        headers = {
            "Authorization": f"Bearer {self._refresh_token}",
            "Content-Type": "application/json",
        }

        try:
            response = self._session.post(
                refresh_url, headers=headers, timeout=self._timeout
            )
            response.raise_for_status()

            self._access_token = response.json().get("access_token")
            if not self._access_token:
                raise SupersetApiError("Access token not received on refresh")

            # Update expiry
            try:
                payload_decoded = jwt.decode(
                    self._access_token, options={"verify_signature": False}
                )
                exp = payload_decoded.get("exp")
                if exp:
                    self._access_exp = datetime.fromtimestamp(
                        exp, timezone.utc
                    )
            except Exception as e:
                logger.warning("Failed to decode JWT expiry: %s", e)

            # Refresh CSRF token
            self._fetch_csrf_token()

            logger.info("Successfully refreshed access token")

        except requests.RequestException as e:
            logger.error("Token refresh failed: %s", e)
            raise SupersetApiError(f"Token refresh failed: {e}") from e

    def _ensure_authenticated(self) -> None:
        """Ensure we have valid authentication, refreshing if needed."""
        # Initial authentication
        if not self._access_token:
            self._authenticate()
            return

        # Check if token is expired and refresh if needed
        if self._access_exp and self._access_exp <= datetime.now(timezone.utc):
            logger.debug("Access token expired, refreshing")
            try:
                self._refresh_access_token()
            except SupersetApiError:
                # If refresh fails, try full re-authentication
                logger.warning("Token refresh failed, re-authenticating")
                self._authenticate()

    def _send_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an authenticated request to the Superset API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE).
            endpoint: API endpoint path.
            params: URL query parameters.
            payload: JSON payload for request body.

        Returns:
            Parsed JSON response.

        Raises:
            SupersetApiError: If the request fails.
        """
        self._ensure_authenticated()

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-CSRF-Token": self._csrf_token or "",
            "Referer": f"{self.base_url}/api/v1/security/csrf_token/",
        }

        try:
            response = self._session.request(
                method,
                url,
                params=params,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException as e:
            logger.error("Request error for %s %s: %s", method, url, e)
            raise SupersetApiError(
                f"API {method} {endpoint} request failed: {e}"
            ) from e

    def _paginated_get(
        self,
        endpoint: str,
        filters: list[dict[str, Any]] | None = None,
        page_size: int = MAX_PAGE_SIZE,
        max_pages: int = MAX_PAGES,
    ) -> list[dict[str, Any]]:
        """Fetch all results from a paginated endpoint.

        Iterates through pages until all results are retrieved or
        ``max_pages`` is reached.

        Args:
            endpoint: API endpoint path.
            filters: Server-side filter dicts, for example
                ``[{"col": "name", "opr": "eq", "value": "Admin"}]``.
            page_size: Number of results per page.
            max_pages: Number of pages to fetch.

        Returns:
            Combined list of all result dicts.
        """
        all_results: list[dict[str, Any]] = []

        for page in range(max_pages):
            q_params: dict[str, Any] = {
                "page": page,
                "page_size": page_size,
            }
            if filters:
                q_params["filters"] = filters

            q = quote_plus(json.dumps(q_params))
            response = self._send_request("GET", f"{endpoint}?q={q}")

            results = response.get("result", [])
            all_results.extend(results)

            total = response.get("count", 0)
            fetched = (page + 1) * page_size
            if fetched >= total:
                break

        return all_results

    @staticmethod
    def _build_trino_connection_string(
        host_port: str,
        trino_catalog: str,
        username: str,
        password: str,
        use_ssl: bool,
    ) -> str:
        """Build the SQLAlchemy URI for a Trino connection.

        Args:
            host_port: Trino server host:port.
            trino_catalog: Trino catalog name.
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to enable SSL.

        Returns:
            str: The SQLAlchemy URI for the Trino connection.
        """
        encoded_user, encoded_pass = quote_plus(username), quote_plus(password)

        if use_ssl:
            uri = f"trino://{encoded_user}:{encoded_pass}@{host_port}/{trino_catalog}"
        else:
            uri = f"trino://{encoded_user}@{host_port}/{trino_catalog}"

        return uri

    @staticmethod
    def _get_default_trino_database_payload(
        database_name: str, sqlalchemy_uri: str
    ) -> dict[str, Any]:
        """Get the default payload for creating a Trino database in Superset.

        Args:
            database_name: Name for the database in Superset.
            sqlalchemy_uri: SQLAlchemy connection URI.

        Returns:
            Default payload dict for Trino database creation.
        """
        return {
            "engine": "trino",
            "database_name": database_name,
            "configuration_method": "sqlalchemy_form",
            "sqlalchemy_uri": sqlalchemy_uri,
            "engine_information": {
                "disable_ssh_tunneling": False,
                "supports_file_upload": True,
            },
            "extra": json.dumps(
                {
                    "allows_virtual_table_explore": True,
                    "cost_estimate_enabled": True,
                }
            ),
            "expose_in_sqllab": True,
            "allow_run_async": True,
            "impersonate_user": True,
        }

    def get_trino_databases(self) -> list[TrinoConnection]:
        """Get existing Trino database connections.

        Returns:
            List of TrinoConnection objects for all Trino databases.
        """
        databases = self._paginated_get("/api/v1/database/")
        trino_connections: list[TrinoConnection] = []
        for db in databases:
            backend = db.get("backend", "")
            if backend != "trino":
                continue

            db_id = db.get("id")
            db_name = db.get("database_name")
            if not db_id or not db_name:
                continue

            # Fetch detailed connection info for this database
            try:
                connection_info = self._send_request(
                    "GET", f"/api/v1/database/{db_id}/connection"
                )

                uri = connection_info.get("result", {}).get("sqlalchemy_uri")
                if uri and "trino://" in uri:
                    catalog = uri.rsplit("/", 1)[-1].split("?")[0]
                    trino_connections.append(
                        TrinoConnection(
                            id=db_id,
                            database_name=db_name,
                            sqlalchemy_uri=uri,
                            catalog=catalog,
                        )
                    )
            except (KeyError, AttributeError, SupersetApiError) as e:
                logger.warning(
                    "Error fetching/parsing Trino connection '%s' (id=%s): %s",
                    db_name,
                    db_id,
                    e,
                )
                continue

        logger.debug(
            "Found %d Trino connections out of %d total databases",
            len(trino_connections),
            len(databases),
        )
        return trino_connections

    def create_trino_database(  # pylint: disable=too-many-positional-arguments
        self,
        database_name: str,
        trino_catalog: str,
        host_port: str,
        username: str,
        password: str,
        use_ssl: bool = True,
    ) -> dict[str, Any]:
        """Create a Trino database connection in Superset.

        Args:
            database_name: Name for the database in Superset.
            trino_catalog: Trino catalog name.
            host_port: Trino server host:port.
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to use SSL for the connection.

        Returns:
            API response dict with created database info.
        """
        sqlalchemy_uri = self._build_trino_connection_string(
            host_port, trino_catalog, username, password, use_ssl
        )

        payload = self._get_default_trino_database_payload(
            database_name, sqlalchemy_uri
        )

        logger.info(
            "Creating Superset database '%s' for Trino catalog '%s'",
            database_name,
            trino_catalog,
        )

        response = self._send_request(
            "POST", "/api/v1/database/", payload=payload
        )

        db_id = response.get("id", "N/A")
        logger.info(
            "Created Superset database '%s' (id=%s)", database_name, db_id
        )
        return response

    def update_trino_database(  # pylint: disable=too-many-positional-arguments
        self,
        database_id: int,
        host_port: str,
        trino_catalog: str,
        username: str,
        password: str,
        use_ssl: bool = True,
    ) -> dict[str, Any]:
        """Update URI for an existing Trino database.

        Args:
            database_id: Superset database ID.
            host_port: Trino server host:port.
            trino_catalog: Trino catalog name.
            username: Trino username.
            password: Trino password.
            use_ssl: Whether to use SSL for the connection.

        Returns:
            API response dict.
        """
        sqlalchemy_uri = self._build_trino_connection_string(
            host_port, trino_catalog, username, password, use_ssl
        )
        payload = {"sqlalchemy_uri": sqlalchemy_uri}

        response = self._send_request(
            "PUT",
            f"/api/v1/database/{database_id}",
            payload=payload,
        )

        logger.info(
            "Updated Trino database id=%s (catalog=%s)",
            database_id,
            trino_catalog,
        )
        return response

    def get_role_id(self, role_name: str) -> int | None:
        """Find a role by name and return its ID.

        Args:
            role_name: The name of the role to find.

        Returns:
            Role ID if found, None otherwise.
        """
        results = self._paginated_get(
            "/api/v1/security/roles/",
            filters=[{"col": "name", "opr": "eq", "value": role_name}],
        )

        if results:
            role_id = results[0].get("id")
            if role_id:
                return role_id

        logger.warning("Role '%s' not found via Superset API", role_name)
        return None

    def get_database_access_permission_id(
        self, database_name: str
    ) -> int | None:
        """Find the permission_view_menu ID for database_access on a database.

        Paginates through ``GET /api/v1/security/permissions-resources/``
        to find the entry where ``permission.name == 'database_access'``
        and ``view_menu.name`` contains the database name.

        Args:
            database_name: The Superset database name.

        Returns:
            The permission_view_menu ID if found, None otherwise.
        """
        results = self._paginated_get(
            "/api/v1/security/permissions-resources/"
        )

        for perm in results:
            perm_name = perm.get("permission", {}).get("name", "")
            view_name = perm.get("view_menu", {}).get("name", "")
            if (
                perm_name == "database_access"
                and f"[{database_name}]" in view_name
            ):
                return perm["id"]

        logger.warning(
            "database_access permission for '%s' not found", database_name
        )
        return None

    def get_role_permission_ids(self, role_id: int) -> list[int]:
        """Get the current permission_view_menu IDs for a role.

        Args:
            role_id: The Superset role ID.

        Returns:
            List of permission_view_menu IDs (empty if none or on error).
        """
        response = self._send_request(
            "GET", f"/api/v1/security/roles/{role_id}/permissions/"
        )

        results = response.get("result", [])

        return [p["id"] for p in results if isinstance(p, dict) and "id" in p]

    def update_role_permissions(
        self, role_id: int, permission_view_menu_id: int
    ) -> None:
        """Grant a permission to a role, preserving existing permissions.

        Fetches the role's current permission IDs, skips if already
        present, otherwise POSTs the full list (existing + new).

        Args:
            role_id: The Superset role ID.
            permission_view_menu_id: The permission_view_menu ID to grant.
        """
        existing_ids = self.get_role_permission_ids(role_id)

        if permission_view_menu_id in existing_ids:
            logger.debug(
                "Permission %s already granted to role %s, skipping",
                permission_view_menu_id,
                role_id,
            )
            return

        all_ids = existing_ids + [permission_view_menu_id]
        payload = {"permission_view_menu_ids": all_ids}

        self._send_request(
            "POST",
            f"/api/v1/security/roles/{role_id}/permissions",
            payload=payload,
        )

        logger.info(
            "Granted permission %s to role %s",
            permission_view_menu_id,
            role_id,
        )
