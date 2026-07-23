import os

from flask_appbuilder.models.sqla.filters import FilterContains
from flask_appbuilder.security.sqla.apis import PermissionViewMenuApi
from superset.security import SupersetSecurityManager


class _SupersetPermissionViewMenuApi(PermissionViewMenuApi):
    """Workaround for https://github.com/apache/superset/issues/40293.

    Flask-AppBuilder's PermissionViewMenuApi does not declare search_columns,
    so every filter query to /api/v1/security/permissions-resources/ fails with
    "Filter column: id not allowed to filter". Declaring them here and
    registering the RelationshipToManyFilter handlers for the dot-path columns
    restores filtering for the List Roles UI and for any direct API consumer.
    """

    search_columns = ["id", "permission.name", "view_menu.name"]

    def _init_properties(self) -> None:
        super()._init_properties()
        for col in ["permission.name", "view_menu.name"]:
            self._filters._search_filters[col] = [
                FilterContains(col, self.datamodel)
            ]


class CustomSecurityManager(SupersetSecurityManager):
    permission_view_menu_api = _SupersetPermissionViewMenuApi

    def raise_for_access(self, *args, **kwargs):
        """Authorize temporary SQL Lab queries for their owner.

        This works around apache/superset#39296 for Superset 6.1.0. Query
        does not retain execution-time template parameters, so templated SQL
        is rerouted only when database-level access is already sufficient.
        """
        if (
            os.getenv("ENABLE_RAISE_FOR_ACCESS_PATCH", "false").lower()
            != "true"
        ):
            return super().raise_for_access(*args, **kwargs)

        datasource = kwargs.get("datasource")
        if datasource is not None and "query" not in kwargs:
            from superset.models.sql_lab import Query

            if isinstance(datasource, Query):
                from flask import g

                cur_uid = getattr(getattr(g, "user", None), "id", None)
                q_uid = getattr(datasource, "user_id", None)
                is_owner = (
                    cur_uid is not None
                    and q_uid is not None
                    and cur_uid == q_uid
                )
                is_admin = self.is_admin()
                database = getattr(datasource, "database", None)
                sql = getattr(datasource, "sql", None)
                has_query_context = (
                    database is not None
                    and isinstance(sql, str)
                    and bool(sql.strip())
                )
                is_templated = isinstance(sql, str) and (
                    "{{" in sql or "{%" in sql
                )
                can_reroute = is_admin or (
                    is_owner
                    and has_query_context
                    and (
                        not is_templated or self.can_access_database(database)
                    )
                )
                if can_reroute:
                    fwd = {
                        k: v for k, v in kwargs.items() if k != "datasource"
                    }
                    fwd["query"] = datasource
                    return super().raise_for_access(*args, **fwd)
        return super().raise_for_access(*args, **kwargs)

    def oauth_user_info(self, provider, response=None):
        if provider == "google":
            me = self.appbuilder.sm.oauth_remotes[provider].get(
                "https://openidconnect.googleapis.com/v1/userinfo"
            )
            data = me.json()
            return {
                "name": data["name"],
                "email": data["email"],
                "id": data["sub"],
                "username": data["email"],
                "first_name": data["given_name"],
                "last_name": data["family_name"],
            }
