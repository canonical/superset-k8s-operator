import os
from cachelib.redis import RedisCache
from celery.schedules import crontab

# Redis caching
CACHE_CONFIG = {
    "CACHE_TYPE": "redis",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/0",
}
# TALISMAN_ENABLED=True
FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/1",
}
EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/2",
}
DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "SupersetMetastoreCache",
    "CACHE_KEY_PREFIX": "superset_results",
    "CACHE_DEFAULT_TIMEOUT": 86400,
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/3",
}

RESULTS_BACKEND = RedisCache(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    key_prefix="superset_results",
)
TALISMAN_ENABLED = True
TALISMAN_CONFIG = {
    "content_security_policy": {
        "default-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "worker-src": ["'self'", "blob:"],
        "connect-src": [
            "'self'",
            "https://api.mapbox.com",
            "https://events.mapbox.com",
        ],
        "object-src": "'none'",
        "style-src": ["'self'", "'unsafe-inline'"],
        "script-src": ["'self'", "'unsafe-inline'"],
    },
    "force_https": False,
}

SQLALCHEMY_POOL_SIZE = int(os.getenv('SQLALCHEMY_POOL_SIZE'))
SQLALCHEMY_POOL_TIMEOUT = int(os.getenv('SQLALCHEMY_POOL_TIMEOUT'))
SQLALCHEMY_MAX_OVERFLOW = int(os.getenv('SQLALCHEMY_MAX_OVERFLOW'))

class CeleryConfig(object):
    broker_url = f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/4"
    imports = (
        "superset.sql_lab",
        "superset.tasks",
    )
    result_backend = f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/5"
    worker_log_level = "DEBUG"
    worker_prefetch_multiplier = 10
    task_acks_late = True
    task_annotations = {
        "sql_lab.get_sql_results": {
            "rate_limit": "100/s",
        },
        "email_reports.send": {
            "rate_limit": "1/s",
            "time_limit": 120,
            "soft_time_limit": 150,
            "ignore_result": True,
        },
    }
    beat_schedule = {
        'reports.scheduler': {
            'task': 'reports.scheduler',
            'schedule': crontab(minute='*', hour='*'),
        },
        'reports.prune_log': {
            'task': 'reports.prune_log',
            'schedule': crontab(minute=0, hour=0),
        },
        "cache-warmup-daily": {
            "task": "cache-warmup",
            "schedule": crontab(minute="1", hour="9"),  # UTC @daily
            "kwargs": {
                "strategy_name": "top_n_dashboards",
                "top_n": 10,
                "since": "7 days ago",
            },
        }
    }


CELERY_CONFIG = CeleryConfig

FEATURE_FLAGS = {
    flag_name: os.getenv(flag_name, '').lower() != 'false'
    for flag_name in [
        'ALERTS_ATTACH_REPORTS',
        'DASHBOARD_CROSS_FILTERS',
        'DASHBOARD_RBAC',
        'EMBEDDABLE_CHARTS',
        'SCHEDULED_QUERIES',
        'ESTIMATE_QUERY_COST',
        'ENABLE_TEMPLATE_PROCESSING',
        'ALERT_REPORTS'
    ]
}

SECRET_KEY = os.getenv("SUPERSET_SECRET_KEY")

LOG_LEVEL = "DEBUG"
TIME_ROTATE_LOG_LEVEL = "DEBUG"
ENABLE_TIME_ROTATE = True

# postgresql metadata db
SQLALCHEMY_DATABASE_URI = os.getenv("SQL_ALCHEMY_URI")
