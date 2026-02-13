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
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(os.getenv("REDIS_TIMEOUT", 300)),
    "CACHE_REDIS_HOST": os.getenv("REDIS_HOST"),
    "CACHE_REDIS_PORT": int(os.getenv("REDIS_PORT")),
    "CACHE_REDIS_DB": 0,
}
# TALISMAN_ENABLED=True
FILTER_STATE_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(os.getenv("REDIS_TIMEOUT", 300)),
    "CACHE_KEY_PREFIX": "superset_filter_cache",
    "CACHE_REDIS_HOST": os.getenv("REDIS_HOST"),
    "CACHE_REDIS_PORT": int(os.getenv("REDIS_PORT")),
    "CACHE_REDIS_DB": 1,
}
EXPLORE_FORM_DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(os.getenv("REDIS_TIMEOUT", 300)),
    "CACHE_KEY_PREFIX": "superset_explore_cache",
    "CACHE_REDIS_HOST": os.getenv("REDIS_HOST"),
    "CACHE_REDIS_PORT": int(os.getenv("REDIS_PORT")),
    "CACHE_REDIS_DB": 2,
}
DATA_CACHE_CONFIG = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_DEFAULT_TIMEOUT": int(os.getenv("REDIS_TIMEOUT", 300)),
    "CACHE_REDIS_HOST": os.getenv("REDIS_HOST"),
    "CACHE_REDIS_PORT": int(os.getenv("REDIS_PORT")),
    "CACHE_REDIS_DB": 3,
}

RESULTS_BACKEND = RedisCache(
    host=os.getenv("REDIS_HOST"),
    port=int(os.getenv("REDIS_PORT")),
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

if os.getenv("ALERT_REPORTS", "").lower() == "true":
    beat_schedule_config.update({"reports.scheduler": {
        "task": "reports.scheduler",
        "schedule": crontab(minute="*", hour="*"),
        },
    })
    
    # https://superset.apache.org/docs/configuration/alerts-reports/
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = int(os.getenv("SMTP_PORT", 0)) or None
    SMTP_STARTTLS = os.getenv("SMTP_STARTTLS", "").lower() == "true"
    SMTP_SSL_SERVER_AUTH = os.getenv("SMTP_SSL_SERVER_AUTH", "").lower() == "true"
    SMTP_SSL = os.getenv("SMTP_SSL", "").lower() == "true"
    SMTP_USER = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_MAIL_FROM = os.getenv("SMTP_EMAIL")
    EMAIL_REPORTS_SUBJECT_PREFIX = os.getenv("SMTP_EMAIL_SUBJECT_PREFIX")
    ALERT_REPORTS_NOTIFICATION_DRY_RUN = False
    
    WEBDRIVER_TYPE = "firefox"
    WEBDRIVER_OPTION_ARGS = [
        "--headless",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-extensions",
    ]
    
    WEBDRIVER_BASEURL_USER_FRIENDLY = os.getenv("SMTP_SUPERSET_EXTERNAL_URL")

# Celery cache warm-up
class CeleryConfig(object):
    broker_url = (
        f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/4"
    )
    imports = (
        "superset.sql_lab",
        "superset.tasks",
        "superset.tasks.async_queries",
    )
    result_backend = (
        f"redis://{os.getenv('REDIS_HOST')}:{os.getenv('REDIS_PORT')}/5"
    )
    worker_log_level = "DEBUG"
    worker_prefetch_multiplier = 1
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

# based on https://github.com/apache/superset/blob/6.0.0/RESOURCES/FEATURE_FLAGS.md
SUPPORTED_FEATURE_FLAGS = [
    # In Development
    "ALERT_REPORT_TABS",
    "CHART_PLUGINS_EXPERIMENTAL",
    "DATE_RANGE_TIMESHIFTS_ENABLED",
    "ENABLE_ADVANCED_DATA_TYPES",
    "PRESTO_EXPAND_DATA",
    "SHARE_QUERIES_VIA_KV_STORE",
    "TAGGING_SYSTEM",

    # In Testing
    "ALERT_REPORTS",
    "ALLOW_FULL_CSV_EXPORT",
    "CACHE_IMPERSONATION",
    "CONFIRM_DASHBOARD_DIFF",
    "DATE_FORMAT_IN_EMAIL_SUBJECT",
    "DYNAMIC_PLUGINS",
    "ENABLE_SUPERSET_META_DB",
    "ESTIMATE_QUERY_COST",
    "GLOBAL_ASYNC_QUERIES",
    "IMPERSONATE_WITH_EMAIL_PREFIX",
    "PLAYWRIGHT_REPORTS_AND_THUMBNAILS",
    "RLS_IN_SQLLAB",
    "SSH_TUNNELING",
    "USE_ANALOGOUS_COLORS",

    # Stable - Launch/Deprecation Path
    "DASHBOARD_VIRTUALIZATION",

    # Stable - Runtime Configuration
    "ALERTS_ATTACH_REPORTS",
    "ALLOW_ADHOC_SUBQUERY",
    "DASHBOARD_RBAC",
    "DATAPANEL_CLOSED_BY_DEFAULT",
    "DRILL_BY",
    "DRUID_JOINS",
    "EMBEDDABLE_CHARTS",
    "EMBEDDED_SUPERSET",
    "ENABLE_TEMPLATE_PROCESSING",
    "ESCAPE_MARKDOWN_HTML",
    "LISTVIEWS_DEFAULT_CARD_VIEW",
    "SCHEDULED_QUERIES",
    "SLACK_ENABLE_AVATARS",
    "SQL_VALIDATORS_BY_ENGINE",
    "SQLLAB_BACKEND_PERSISTENCE",
    "THUMBNAILS",

    # Deprecated
    "AVOID_COLORS_COLLISION",
    "DRILL_TO_DETAIL",
    "ENABLE_JAVASCRIPT_CONTROLS",
    "KV_STORE"
]

FEATURE_FLAGS = {
    flag_name: os.getenv(flag_name, "").lower() == "true"
    for flag_name in SUPPORTED_FEATURE_FLAGS
    if os.getenv(flag_name, "")
}

# Asynchronous queries
GLOBAL_ASYNC_QUERIES_REDIS_STREAM_PREFIX = "async-events-"
GLOBAL_ASYNC_QUERIES_JWT_SECRET = os.environ["GLOBAL_ASYNC_QUERIES_JWT"]
GLOBAL_ASYNC_QUERIES_CACHE_BACKEND = {
    "CACHE_TYPE": "RedisCache",
    "CACHE_KEY_PREFIX": "superset_gaq_",
    "CACHE_DEFAULT_TIMEOUT": int(os.getenv("REDIS_TIMEOUT", 300)),
    "CACHE_REDIS_HOST": os.getenv("REDIS_HOST"),
    "CACHE_REDIS_PORT": int(os.getenv("REDIS_PORT")),
    "CACHE_REDIS_DB": 6,
}
GLOBAL_ASYNC_QUERIES_POLLING_DELAY = int(os.getenv("GLOBAL_ASYNC_QUERIES_POLLING_DELAY", "500"))
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

# Dashboard size limitation
SUPERSET_DASHBOARD_POSITION_DATA_LIMIT = int(os.getenv("DASHBOARD_SIZE_LIMIT", 65535))

# Proxy
HTTP_PROXY = os.getenv("HTTP_PROXY")
HTTPS_PROXY = os.getenv("HTTPS_PROXY")
NO_PROXY = os.getenv("NO_PROXY")

# Werkzeug configurations
# Ref: https://werkzeug.palletsprojects.com/en/stable/request_data/#limiting-request-data
# See: https://github.com/apache/superset/issues/26373
MAX_CONTENT_LENGTH = int(v) if (v := os.getenv("MAX_CONTENT_LENGTH")) else None
MAX_FORM_MEMORY_SIZE = int(v) if (v := os.getenv("MAX_FORM_MEMORY_SIZE")) else 500_000
MAX_FORM_PARTS = int(v) if (v := os.getenv("MAX_FORM_PARTS")) else 1000

def FLASK_APP_MUTATOR(app):
    """Override the Flask app dynamically."""

    # These values are hardcoded in Werkzeug
    # So we force an override
    class ConfiguredLimitRequest(app.request_class):
        max_form_memory_size = MAX_FORM_MEMORY_SIZE
        max_form_parts = MAX_FORM_PARTS

    # Swap the default Request class with our configured one
    app.request_class = ConfiguredLimitRequest
