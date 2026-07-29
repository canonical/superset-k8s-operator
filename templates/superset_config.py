import os
from cachelib.redis import RedisCache
from celery.schedules import crontab
from flask_appbuilder.security.manager import AUTH_OAUTH
from custom_security_manager import CustomSecurityManager
from permission_error_messages import attach_error_rewriter
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

if os.getenv("LOG_RETENTION_ENABLED").lower() != "false":
    # Prune the `logs` table (user action audit log) per LOG_RETENTION_DAYS.
    beat_schedule_config.update({"prune_logs": {
            "task": "prune_logs",
            "schedule": crontab(minute=0, hour=0),
            "kwargs": {"retention_period_days": int(os.getenv("LOG_RETENTION_DAYS"))},
        },
    })

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
    ALERT_REPORTS_NOTIFICATION_DRY_RUN = (
        os.getenv("ALERT_REPORTS_DRY_RUN", "").lower() == "true"
    )

    # The charm configures this in seconds; Playwright expects milliseconds.
    SCREENSHOT_PLAYWRIGHT_DEFAULT_TIMEOUT = (
        int(os.getenv("SCREENSHOT_TIMEOUT", 600)) * 1000
    )

    # The worker process environment is defined by the charm's Pebble layer and
    # does not export PLAYWRIGHT_BROWSERS_PATH, so point Playwright at the
    # Chromium build bundled in the ROCK image regardless of the runtime HOME.
    os.environ.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH", "/opt/playwright-browsers"
    )

    # Superset 6 renders report screenshots with Playwright + Chromium.
    # WEBDRIVER_OPTION_ARGS are passed to playwright.chromium.launch(), so
    # they must be Chromium flags; --no-sandbox and --disable-dev-shm-usage
    # are required for headless Chromium inside a container.
    WEBDRIVER_TYPE = "chrome"
    WEBDRIVER_OPTION_ARGS = [
        "--headless=new",
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

# based on https://superset.apache.org/admin-docs/configuration/feature-flags/
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

# Alerts and reports rely on screenshots. In Superset 6 the supported renderer
# is Playwright + Chromium, so enabling ALERT_REPORTS implicitly turns on the
# Playwright screenshot path; operators do not configure it separately.
if os.getenv("ALERT_REPORTS", "").lower() == "true":
    FEATURE_FLAGS["PLAYWRIGHT_REPORTS_AND_THUMBNAILS"] = True

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

# OAuth configuration
required_auth_vars = [
    "OAUTH_CLIENT_ID",
    "OAUTH_CLIENT_SECRET",
    "OAUTH_ISSUER_URL",
    "OAUTH_AUTHORIZATION_ENDPOINT",
    "OAUTH_TOKEN_ENDPOINT",
    "OAUTH_USERINFO_ENDPOINT",
    "OAUTH_JWKS_ENDPOINT",
]

CUSTOM_SECURITY_MANAGER = CustomSecurityManager
if all(os.getenv(var) for var in required_auth_vars):
    AUTH_TYPE = AUTH_OAUTH
    OAUTH_PROVIDERS = [
        {
            "name": "oidc",
            "icon": "fa-openid",
            "token_key": "access_token",
            "remote_app": {
                "client_id": os.getenv("OAUTH_CLIENT_ID"),
                "client_secret": os.getenv("OAUTH_CLIENT_SECRET"),
                "api_base_url": os.getenv("OAUTH_ISSUER_URL"),
                "client_kwargs": {"scope": os.getenv("OAUTH_SCOPE", "openid email profile")},
                "request_token_url": None,
                "access_token_url": os.getenv("OAUTH_TOKEN_ENDPOINT"),
                "authorize_url": os.getenv("OAUTH_AUTHORIZATION_ENDPOINT"),
                "jwks_uri": os.getenv("OAUTH_JWKS_ENDPOINT"),
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

    # Respect the original HTTPS request when TLS terminates at ingress.
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

# URL users are directed to when they hit a Trino/Ranger permission-denied error.
DATA_ACCESS_REQUEST_URL = os.getenv("DATA_ACCESS_REQUEST_URL")

def FLASK_APP_MUTATOR(app):
    """Override the Flask app dynamically."""

    # These values are hardcoded in Werkzeug
    # So we force an override
    class ConfiguredLimitRequest(app.request_class):
        max_form_memory_size = MAX_FORM_MEMORY_SIZE
        max_form_parts = MAX_FORM_PARTS

    # Swap the default Request class with our configured one
    app.request_class = ConfiguredLimitRequest

    # Rewrite Trino/Ranger permission-denied errors into user-friendly messages
    attach_error_rewriter(app, request_url=DATA_ACCESS_REQUEST_URL)


# =============================================================================
# Fix: QueryObject cache-key SQL rendering (Apache Superset issue #37114)
# =============================================================================
#
# Under GLOBAL_ASYNC_QUERIES the Celery worker and the UI gunicorn process both
# independently compute QueryObject.cache_key() for the same chart request. The
# two sides produce different SHA-256 hashes for the same chart and the UI gets
# HTTP 422 "Error loading data from cache".
#
# Root cause: the worker hashes the raw form_data it received; the UI hashes the
# form_data rebuilt from the QueryContext cache (Redis DB0). On that rebuild
# path Superset re-renders every adhoc SQL expression through sqlglot
# (parse -> generate), so the UI's SQL text differs from the worker's even
# though both describe the same query. Confirmed rewrites, all observed in prod:
#
#   worker (raw)                       ui (after sqlglot render)
#   ---------------------------------  ---------------------------------
#   sum(a)+sum(b)                      SUM(a) + SUM(b)         case, spacing
#   SUM(CASE \n WHEN ... \n END)       SUM(CASE WHEN ... END)  newlines
#   x IS NOT NULL                      NOT x IS NULL           structure
#   DATE_DIFF('day', a, b)             DATE_DIFF('DAY', a, b)  literal case
#
# Fix: put both sides through the same transform before hashing by rendering each
# SQL expression with sqlglot using the datasource's own dialect. The worker's
# raw text then renders to exactly the string the UI already holds, so the two
# hashes agree.
#
#   * HASH-ONLY. We patch cache_key(), not __init__. The rendered SQL is used
#     solely to compute the hash; the QueryObject's real sqlExpression is saved
#     and restored around the call, so the SQL actually sent to the database is
#     NEVER modified. The worst a bug here can cause is a cache miss, never
#     altered query results.
#   * DIALECT MATTERS. Rendering under the wrong dialect does not converge, it
#     invents a third spelling: under sqlglot's default dialect DATE_DIFF comes
#     back as DATEDIFF and an identifier is uppercased. The dialect is resolved
#     from the query's own database and never guessed.
#   * MINIMAL FALLBACK. When the dialect cannot be resolved, or sqlglot has no
#     grammar for the expression, only line endings and surrounding whitespace
#     are normalised. An orderby carrying an item list, `col1 DESC, col2`, is not
#     a parsable expression so whitespace diffs would not be fixed by sqlglot.
#
# Upstream reference: Apache Superset PR #38227 performs only CRLF to LF and a
# leading/trailing strip, which addresses only a subset of the divergences.
#
# Remove this block once a Superset release fixes the divergence at source.
# =============================================================================
from superset.common.query_object import QueryObject as _QO

_QO_DIALECTS = {}  # database id -> sqlglot dialect (or None if unresolvable)
_QO_RENDER_CACHE = {}  # (sql, dialect) -> rendered sql (or None if unparsable)
_QO_RENDER_CACHE_MAX = 4096

# SQLAlchemy backend names that sqlglot spells differently. Only consulted when
# Superset's own engine -> dialect mapping is unavailable.
_QO_BACKEND_ALIASES = {
    "postgresql": "postgres",
    "mssql": "tsql",
    "awsathena": "athena",
}


def _qo_dialect(query_object):
    """Resolve the sqlglot dialect for this query's database, or None.

    Both sides resolve identically, same code, same datasource, so the two
    hashes agree. A dialect sqlglot does not recognise is treated as
    unresolvable rather than guessed.
    """
    try:
        database = getattr(getattr(query_object, "datasource", None), "database", None)
        if database is None:
            return None
        key = getattr(database, "id", None) or id(database)
        if key in _QO_DIALECTS:
            return _QO_DIALECTS[key]

        import sqlglot

        candidates = []
        try:
            # Superset's own engine -> sqlglot mapping
            try:
                from superset.sql.parse import SQLGLOT_DIALECTS
            except Exception:
                from superset.sql_parse import SQLGLOT_DIALECTS
            candidates.append(SQLGLOT_DIALECTS.get(database.db_engine_spec.engine))
        except Exception:
            pass
        try:
            # SQLAlchemy backend name, e.g. "trino"
            backend = database.url_object.get_backend_name()
            candidates.append(_QO_BACKEND_ALIASES.get(backend, backend))
        except Exception:
            pass

        dialect = None
        for candidate in candidates:
            if not candidate:
                continue
            try:
                sqlglot.Dialect.get_or_raise(candidate)
                dialect = candidate
                break
            except Exception:
                continue
        _QO_DIALECTS[key] = dialect
        return dialect
    except Exception:  # pragma: no cover - never break cache_key()
        return None


def _qo_trim(sql):
    """Line endings and surrounding whitespace only."""
    return sql.replace("\r\n", "\n").replace("\r", "\n").strip()


def _qo_render_sql(sql, dialect):
    """Render one SQL expression the way the UI holds it, for hashing only.

    Falls back to trimming the ends when there is no dialect to render with,
    or when sqlglot cannot parse the expression.
    """
    if dialect is None:
        return _qo_trim(sql)
    key = (sql, str(dialect))
    if key not in _QO_RENDER_CACHE:
        try:
            import sqlglot

            rendered = sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect)
        except Exception:
            rendered = None
        if len(_QO_RENDER_CACHE) >= _QO_RENDER_CACHE_MAX:
            _QO_RENDER_CACHE.clear()
        _QO_RENDER_CACHE[key] = rendered
    rendered = _QO_RENDER_CACHE[key]
    return _qo_trim(sql) if rendered is None else rendered


def _qo_render_expr(expr, dialect):
    """Return a copy of an adhoc SQL dict with its sqlExpression rendered."""
    if isinstance(expr, dict) and expr.get("expressionType") == "SQL":
        sql = expr.get("sqlExpression")
        if isinstance(sql, str):
            patched = dict(expr)
            patched["sqlExpression"] = _qo_render_sql(sql, dialect)
            return patched
    return expr


def _qo_render_orderby(item, dialect):
    if isinstance(item, (list, tuple)) and item:
        return [_qo_render_expr(item[0], dialect)] + list(item[1:])
    return item


_qo_orig_cache_key = _QO.cache_key


def _qo_patched_cache_key(self, **extra):
    # Swap in rendered SQL only for the duration of the hash computation, then
    # restore the originals so the executed query is left untouched.
    saved = (self.metrics, self.columns, self.orderby, self.series_limit_metric)
    try:
        dialect = _qo_dialect(self)
        self.metrics = [_qo_render_expr(m, dialect) for m in (self.metrics or [])]
        self.columns = [_qo_render_expr(c, dialect) for c in (self.columns or [])]
        self.orderby = [_qo_render_orderby(o, dialect) for o in (self.orderby or [])]
        if self.series_limit_metric is not None:
            self.series_limit_metric = _qo_render_expr(self.series_limit_metric, dialect)
        return _qo_orig_cache_key(self, **extra)
    finally:
        (
            self.metrics,
            self.columns,
            self.orderby,
            self.series_limit_metric,
        ) = saved


_QO.cache_key = _qo_patched_cache_key
# =============================================================================
# End fix: QueryObject cache-key SQL rendering
# =============================================================================

# MCP server configuration
# Ref: https://superset.apache.org/admin-docs/6.1.0/configuration/mcp-server/
if os.getenv("MCP_AUTH_ENABLED", "").lower() == "false":
    MCP_AUTH_ENABLED = False

_mcp_dev_username = os.getenv("MCP_DEV_USERNAME", "")
if _mcp_dev_username:
    MCP_DEV_USERNAME = _mcp_dev_username

_mcp_jwt_secret = os.getenv("MCP_JWT_SECRET", "")
if _mcp_jwt_secret:
    MCP_AUTH_ENABLED = True
    MCP_JWT_ALGORITHM = "HS256"
    MCP_JWT_SECRET = _mcp_jwt_secret
    MCP_JWT_ISSUER = "superset-k8s"
    MCP_JWT_AUDIENCE = "superset-mcp"

    # Patch get_user_from_request to bridge FastMCP's validated JWT access
    # token to Flask's g.user. In Superset 6.1.0, MCP_USER_RESOLVER is
    # documented but not wired into get_user_from_request(), so the JWT
    # sub claim is never used to load a Superset user. This patch adds
    # that missing step between JWT validation and tool execution.
    def _get_user_from_request_with_jwt():
        from flask import current_app, g

        if hasattr(g, "user") and g.user:
            return g.user

        try:
            import sys
            from fastmcp.server.dependencies import get_access_token
            from superset.mcp_service.auth import load_user_with_relationships

            token = get_access_token()
            with open("/tmp/mcp_patch_debug.txt", "a") as _f:
                _f.write(f"token={type(token).__name__}: {token}\n")
            if token is not None:
                claims = getattr(token, "claims", {}) or {}
                username = (
                    getattr(token, "subject", None)
                    or claims.get("sub")
                    or claims.get("email")
                    or claims.get("username")
                )
                with open("/tmp/mcp_patch_debug.txt", "a") as _f:
                    _f.write(f"username={username}\n")
                if username:
                    user = load_user_with_relationships(username)
                    if user:
                        return user
        except Exception as e:
            import sys
            with open("/tmp/mcp_patch_debug.txt", "a") as _f:
                _f.write(f"exception: {type(e).__name__}: {e}\n")

        dev_username = current_app.config.get("MCP_DEV_USERNAME")
        if dev_username:
            from superset.mcp_service.auth import load_user_with_relationships

            user = load_user_with_relationships(dev_username)
            if user:
                return user

        auth_enabled = current_app.config.get("MCP_AUTH_ENABLED", False)
        jwt_configured = bool(current_app.config.get("MCP_JWT_SECRET"))
        raise ValueError(
            "No authenticated user found. Tried:\n"
            f"  - g.user was not set by JWT middleware "
            f"(MCP_AUTH_ENABLED={auth_enabled}, JWT keys configured={jwt_configured})\n"
            "  - MCP_DEV_USERNAME is not configured\n\n"
            "Either pass a valid JWT bearer token or configure MCP_DEV_USERNAME."
        )

    import superset.mcp_service.auth as _mcp_auth_module
    _mcp_auth_module.get_user_from_request = _get_user_from_request_with_jwt
