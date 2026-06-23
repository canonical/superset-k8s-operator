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


class CustomSsoSecurityManager(SupersetSecurityManager):
    permission_view_menu_api = _SupersetPermissionViewMenuApi

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
