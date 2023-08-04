import os
from cachelib.redis import RedisCache

redis_host = os.getenv("REDIS_HOST")
redis_port = os.getenv("REDIS_PORT")

print(f"redis host: {redis_host}")
# Redis caching
CACHE_CONFIG = {
    "CACHE_TYPE": "redis",
    "CACHE_REDIS_URL": f"redis://{redis_host}:{redis_port}/0",
}
# TALISMAN_ENABLED=True
FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_URL": f"redis://{redis_host}:{redis_port}/1",
}
EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_URL": f"redis://{redis_host}:{redis_port}/2",
}
DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "SupersetMetastoreCache",
    "CACHE_KEY_PREFIX": "superset_results",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_REDIS_URL": f"redis://{redis_host}:{redis_port}/3",
}

RESULTS_BACKEND = RedisCache(
    host=redis_host, port=redis_port, key_prefix="superset_results"
)


secret = os.getenv("SUPERSET_SECRET_KEY")
if secret:
    SECRET_KEY = secret

LOG_LEVEL = "DEBUG"
TIME_ROTATE_LOG_LEVEL = "DEBUG"
ENABLE_TIME_ROTATE = True
