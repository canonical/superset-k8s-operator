import os
from cachelib.redis import RedisCache
from celery.schedules import crontab
from flask_appbuilder.security.manager import AUTH_OAUTH
from custom_sso_security_manager import CustomSsoSecurityManager
from sentry_interceptor import redact_params
from superset.stats_logger import StatsdStatsLogger
import sentry_sdk
import yaml


APPLICATION_PORT = os.getenv("APPLICATION_PORT")
SERVER_ALIAS = os.getenv("SERVER_ALIAS")

# Monitoring with Sentry
SENTRY_DSN = os.getenv("SENTRY_DSN")
SENTRY_ENVIRONMENT = os.getenv("SENTRY_ENVIRONMENT")
SENTRY_RELEASE = os.getenv("SENTRY_RELEASE")
SENTRY_SAMPLE_RATE = os.getenv("SENTRY_SAMPLE_RATE")
SENTRY_REDACT_PARAMS = os.getenv("SENTRY_REDACT_PARAMS").lower() != "false"

sentry_before_send = None
if SENTRY_REDACT_PARAMS:
    sentry_before_send = redact_params

if all([SENTRY_DSN, SENTRY_ENVIRONMENT, SENTRY_RELEASE]):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=SENTRY_ENVIRONMENT,
        release=SENTRY_RELEASE,
        sample_rate=float(SENTRY_SAMPLE_RATE),
        before_send=sentry_before_send,
        )

# StatsD logging
STATS_LOGGER = StatsdStatsLogger(host="localhost", port=os.getenv("STATSD_PORT"))

PREFERRED_DATABASE = [
    "PostgreSQL",
    "Trino",
    "MySQL",
]

# Redis caching
CACHE_CONFIG = {
    "CACHE_TYPE": "redis",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/0",
}
# TALISMAN_ENABLED=True
FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400, # 24 hours
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/1",
}
EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": 86400, # 24 hours
    "CACHE_KEY_PREFIX": "superset_explore_cache",
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/2",
}
DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "SupersetMetastoreCache",
    "CACHE_KEY_PREFIX": "superset_results",
    "CACHE_DEFAULT_TIMEOUT": 86400, # 24 hours
    "CACHE_REDIS_URL": f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/3",
}

RESULTS_BACKEND = RedisCache(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    key_prefix="superset_results",
)

TALISMAN_ENABLED = True

image_allow_list = ["'self'", "data:"]
image_domains = os.getenv("ALLOW_IMAGE_DOMAINS")

if image_domains:
    image_allow_list.extend(domain.strip() for domain in image_domains.split(','))

TALISMAN_CONFIG = {
     "force_https": False,
     "content_security_policy": {
        "default-src": ["'self'", "'unsafe-inline'", "'unsafe-eval'"],
        "img-src": image_allow_list,
        "worker-src": ["'self'", "blob:"],
        "connect-src": ["'self'", "https://api.mapbox.com", "https://events.mapbox.com"],
        "object-src": "'none'",
     },
     "session_cookie_secure": False,
}

SQLALCHEMY_POOL_SIZE = int(os.getenv("SQLALCHEMY_POOL_SIZE"))
SQLALCHEMY_POOL_TIMEOUT = int(os.getenv("SQLALCHEMY_POOL_TIMEOUT"))
SQLALCHEMY_MAX_OVERFLOW = int(os.getenv("SQLALCHEMY_MAX_OVERFLOW"))

beat_schedule_config = {
        "reports.prune_log": {
            "task": "reports.prune_log",
            "schedule": crontab(minute=0, hour=0),
        },
    }

if os.getenv("CACHE_WARMUP", "").lower() != "false":
    beat_schedule_config.update({"cache-warmup-daily": {
            "task": "cache-warmup",
            "schedule": crontab(minute="1", hour="7"),  # UTC @daily
            "kwargs": {
                "strategy_name": "top_n_dashboards",
                "top_n": 10,
                "since": "7 days ago",
            },
        },
    }
    )

# Celery cache warm-up
class CeleryConfig(object):
    broker_url = (
        f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/4"
    )
    imports = (
        "superset.sql_lab",
        "superset.tasks",
    )
    result_backend = (
        f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/5"
    )
    worker_log_level = "DEBUG"
    worker_prefetch_multiplier = 10
    task_acks_late = True
    task_annotations = {
        "sql_lab.get_sql_results": {
            "rate_limit": "100/s",
        },
    }
    beat_schedule = beat_schedule_config


CELERY_CONFIG = CeleryConfig
WEBDRIVER_BASEURL = f"http://{SERVER_ALIAS}:{APPLICATION_PORT}/"

SUPERSET_WEBSERVER_TIMEOUT = int(os.getenv("WEBSERVER_TIMEOUT"))

SUPPORTED_FEATURE_FLAGS = [
        # TESTING
        "ALERT_REPORTS",
        "ALLOW_FULL_CSV_EXPORT",
        "CACHE_IMPERSONATION",
        "CONFIRM_DASHBOARD_DIFF",
        "DASHBOARD_VIRTUALIZATION",
        "DRILL_BY",
        "DRILL_TO_DETAIL",
        "DYNAMIC_PLUGINS",
        "ENABLE_JAVASCRIPT_CONTROLS",
        "ESTIMATE_QUERY_COST",
        "GENERIC_CHART_AXES",
        "GLOBAL_ASYNC_QUERIES",
        "HORIZONTAL_FILTER_BAR",
        "PLAYWRIGHT_REPORTS_AND_THUMBNAILS",
        "RLS_IN_SQLLAB",
        "SSH_TUNNELING",
        "USE_ANALAGOUS_COLORS",

        # STABLE
        "ALERTS_ATTACH_REPORTS",
        "ALLOW_ADHOC_SUBQUERY",
        "DASHBOARD_CROSS_FILTERS",
        "DASHBOARD_RBAC",
        "DATAPANEL_CLOSED_BY_DEFAULT",
        "DISABLE_LEGACY_DATASOURCE_EDITOR",
        "DRUID_JOINS",
        "EMBEDDABLE_CHARTS",
        "EMBEDDED_SUPERSET",
        "ENABLE_TEMPLATE_PROCESSING",
        "ESCAPE_MARKDOWN_HTML",
        "LISTVIEWS_DEFAULT_CARD_VIEW",
        "SCHEDULED_QUERIES",
        "SQLLAB_BACKEND_PERSISTENCE",
        "SQL_VALIDATORS_BY_ENGINE",
        "THUMBNAILS",
    ]

FEATURE_FLAGS = {
    flag_name: os.getenv(flag_name, "").lower() == "true"
    for flag_name in SUPPORTED_FEATURE_FLAGS
    if os.getenv(flag_name, "")
}

# Asynchronous queries
GLOBAL_ASYNC_QUERIES_REDIS_STREAM_PREFIX = "async-events-"
GLOBAL_ASYNC_QUERIES_JWT_SECRET = os.getenv("GLOBAL_ASYNC_QUERIES_JWT")
GLOBAL_ASYNC_QUERIES_REDIS_CONFIG = {
    "port": os.getenv('REDIS_PORT'),
    "host": os.getenv('REDIS_HOST'),
}
GLOBAL_ASYNC_QUERIES_POLLING_DELAY = os.getenv("GLOBAL_ASYNC_QUERIES_POLLING_DELAY")
SECRET_KEY = os.getenv("SUPERSET_SECRET_KEY")

# Log rotation
LOG_LEVEL = "DEBUG"
TIME_ROTATE_LOG_LEVEL = "DEBUG"
ENABLE_TIME_ROTATE = True
FILENAME = os.getenv("LOG_FILE")

# html sanitization
HTML_SANITIZATION = os.getenv("HTML_SANITIZATION").lower() != "false"
HTML_SANITIZATION_SCHEMA_EXTENSIONS = yaml.safe_load(os.getenv("HTML_SANITIZATION_SCHEMA_EXTENSIONS", "{}"))

# postgresql metadata db
SQLALCHEMY_DATABASE_URI = os.getenv("SQL_ALCHEMY_URI")

# OAUTH configuration
required_auth_vars = ["GOOGLE_KEY", "GOOGLE_SECRET", "OAUTH_DOMAIN"]

if all(os.getenv(var) for var in required_auth_vars):
    AUTH_TYPE = AUTH_OAUTH
    CUSTOM_SECURITY_MANAGER = CustomSsoSecurityManager
    OAUTH_PROVIDERS = [
        {
            "name": "google",
            "icon": "fa-google",
            "token_key": "access_token",
            "remote_app": {
                "client_id": os.getenv("GOOGLE_KEY"),
                "client_secret": os.getenv("GOOGLE_SECRET"),
                "api_base_url": "https://www.googleapis.com/oauth2/v2/",
                "client_kwargs": {"scope": "email profile openid"},
                "request_token_url": None,
                "access_token_url": "https://accounts.google.com/o/oauth2/token",
                "authorize_url": "https://accounts.google.com/o/oauth2/auth",
                "authorize_params": {"hd": os.getenv("OAUTH_DOMAIN", "")},
                "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            },
        },
    ]

    # Will allow user self registration, creates Flask user from Authorized User
    AUTH_USER_REGISTRATION = True

    # The custom logic for user self registration role
    admin_users = os.getenv("OAUTH_ADMIN_EMAIL")
    default_role = os.getenv("SELF_REGISTRATION_ROLE")
    AUTH_USER_REGISTRATION_ROLE_JMESPATH = (
        f"contains(['{admin_users}'], email) && 'Admin' || '{default_role}'"
    )

    # For Google https redirect
    ENABLE_PROXY_FIX = True

HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")
NO_PROXY = os.getenv("NO_PROXY")
