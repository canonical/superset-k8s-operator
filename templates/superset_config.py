import os


secret = os.getenv("SUPERSET_SECRET_KEY")
if secret:
    SECRET_KEY = secret

LOG_LEVEL = "DEBUG"
TIME_ROTATE_LOG_LEVEL = "DEBUG"
ENABLE_TIME_ROTATE = True

# postgresql metadata db
metadata_uri = os.getenv("SQL_ALCHEMY_URI")
if metadata_uri: 
    SQLALCHEMY_DATABASE_URI = metadata_uri