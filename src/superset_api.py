# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Superset REST API client for managing database connections and permissions."""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

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
        self._access_token: str | None = None
        self._csrf_token: str | None = None
        self._session_cookie: str | None = None

    def _authenticate(self) -> None:
        """Authenticate with Superset and get tokens.

        Raises:
            SupersetApiError: If authentication fails.
        """
        login_payload = json.dumps(
            {
                "username": self._admin_username,
                "password": self._admin_password,
                "provider": "db",
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/v1/security/login",
            data=login_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                data = json.loads(resp.read())
                self._access_token = data["access_token"]
                if not self._access_token:
                    raise SupersetApiError("Access token not received")
                cookie_header = resp.headers.get("Set-Cookie", "")
                if "session=" in cookie_header:
                    self._session_cookie = cookie_header.split("session=")[
                        1
                    ].split(";")[0]
        except (urllib.error.URLError, KeyError) as e:
            raise SupersetApiError(f"Failed to obtain JWT token: {e}") from e

        csrf_headers = {"Authorization": f"Bearer {self._access_token}"}
        if self._session_cookie:
            csrf_headers["Cookie"] = f"session={self._session_cookie}"

        csrf_req = urllib.request.Request(
            f"{self.base_url}/api/v1/security/csrf_token/",
            headers=csrf_headers,
        )

        try:
            with urllib.request.urlopen(csrf_req, timeout=self._timeout) as resp:  # nosec B310
                data = json.loads(resp.read())
                self._csrf_token = data["result"]
                if not self._csrf_token:
                    raise SupersetApiError("CSRF token not received")
                cookie_header = resp.headers.get("Set-Cookie", "")
                if "session=" in cookie_header:
                    self._session_cookie = cookie_header.split("session=")[
                        1
                    ].split(";")[0]
        except (urllib.error.URLError, KeyError) as e:
            raise SupersetApiError(f"Failed to obtain CSRF token: {e}") from e

    def _send_request(
        self, method: str, endpoint: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send an authenticated request to the Superset API.

        Args:
            method: HTTP method (GET, POST, PUT, etc.).
            endpoint: API endpoint path.
            payload: JSON payload for POST/PUT requests.

        Returns:
            Parsed JSON response.

        Raises:
            SupersetApiError: If the request fails.
        """
        if not self._access_token:
            self._authenticate()

        url = f"{self.base_url}{endpoint}"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-CSRF-Token": self._csrf_token or "",
            "Referer": f"{self.base_url}/api/v1/security/csrf_token/",
        }
        if self._session_cookie:
            headers["Cookie"] = f"session={self._session_cookie}"

        data = json.dumps(payload).encode("utf-8") if payload else None
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logger.error(
                "Superset API %s %s failed (%s): %s",
                method,
                endpoint,
                e.code,
                body,
            )
            raise SupersetApiError(
                f"API {method} {endpoint} failed ({e.code}): {body}",
                status_code=e.code,
            ) from e
        except urllib.error.URLError as e:
            raise SupersetApiError(
                f"API {method} {endpoint} connection error: {e}"
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
