from superset.security import SupersetSecurityManager


class CustomSsoSecurityManager(SupersetSecurityManager):
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
