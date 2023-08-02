import os


secret = os.getenv("SUPERSET_SECRET_KEY")
if secret:
    SECRET_KEY = secret

LOG_LEVEL = "DEBUG"
TIME_ROTATE_LOG_LEVEL = "DEBUG"
ENABLE_TIME_ROTATE = True
