#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Superset charm."""
import logging
from enum import Enum
from typing import Dict, Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

from utils import get_supported_feature_flags

logger = logging.getLogger(__name__)


class BaseEnumStr(str, Enum):
    """Base class for string enum."""

    def __str__(self) -> str:
        """Return the value as a string.

        Returns:
            string of config value
        """
        return str(self.value)


class FunctionType(str, Enum):
    """Enum for the `charm-function` field."""

    app_gunicorn = "app-gunicorn"
    app = "app"
    worker = "worker"
    beat = "beat"


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    allow_image_domains: Optional[str]
    external_hostname: str
    tls_secret_name: Optional[str]
    superset_secret_key: Optional[str]
    admin_password: str
    charm_function: FunctionType
    cache_warmup: bool
    sqlalchemy_pool_size: int
    sqlalchemy_pool_timeout: int
    sqlalchemy_max_overflow: int
    self_registration_role: str
    oauth_admin_email: str
    google_client_id: Optional[str]
    google_client_secret: Optional[str]
    oauth_domain: Optional[str]
    http_proxy: Optional[str]
    https_proxy: Optional[str]
    no_proxy: Optional[str]
    load_examples: bool
    html_sanitization: bool
    html_sanitization_schema_extensions: Optional[str]
    global_async_queries_jwt: Optional[str]
    global_async_queries_polling_delay: int
    sentry_dsn: Optional[str]
    sentry_release: Optional[str]
    sentry_environment: Optional[str]
    sentry_redact_params: bool
    sentry_sample_rate: Optional[str]
    server_alias: str
    webserver_timeout: int
    feature_flags: Optional[str]

    @validator("*", pre=True)
    @classmethod
    def blank_string(cls, value):
        """Check for empty strings.

        Args:
            value: configuration value

        Returns:
            None in place of empty string or value
        """
        if value == "":
            return None
        return value

    @validator("sqlalchemy_pool_size")
    @classmethod
    def sqlalchemy_pool_size_validator(cls, value: str) -> Optional[int]:
        """Check validity of `sqlalchemy_pool_size` field.

        Args:
            value: sqlalchemy-pool-size value

        Returns:
            int_value: integer for sqlalchemy-pool-size configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 0 <= int_value <= 300:
            return int_value
        raise ValueError("Value out of range.")

    @validator("sqlalchemy_pool_timeout")
    @classmethod
    def sqlalchemy_pool_timeout_validator(cls, value: str) -> Optional[int]:
        """Check validity of `sqlalchemy_pool_timeout` field.

        Args:
            value: sqlalchemy-pool-timeout value

        Returns:
            int_value: integer for sqlalchemy-pool-timeout configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 0 <= int_value <= 420:
            return int_value
        raise ValueError("Value out of range.")

    @validator("sqlalchemy_max_overflow")
    @classmethod
    def sqlalchemy_max_overflow_validator(cls, value: str) -> Optional[int]:
        """Check validity of `sqlalchemy_max_overflow` field.

        Args:
            value: sqlalchemy-max-overflow value

        Returns:
            int_value: integer for sqlalchemy-max-overflow configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 0 <= int_value <= 100:
            return int_value
        raise ValueError("Value out of range.")

    @validator("global_async_queries_polling_delay")
    @classmethod
    def global_async_queries_polling_delay_validator(
        cls, value: str
    ) -> Optional[int]:
        """Check validity of `global_async_query_polling_delay` field.

        Args:
            value: global-async-query-polling-delay value

        Returns:
            int_value: integer for global-async-query-polling-delay configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 500 <= int_value <= 5000:
            return int_value
        raise ValueError("Value out of range.")

    @validator("sentry_sample_rate")
    @classmethod
    def sentry_sample_rate_validator(cls, value: str) -> Optional[float]:
        """Check validity of `sentry_sample_rate` field.

        Args:
            value: sentry_sample_rate value

        Returns:
            float_value: float for sentry_sample_rate configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        float_value = float(value)
        if 0 <= float_value <= 1:
            return float_value
        raise ValueError("Value out of range.")

    @validator("webserver_timeout")
    @classmethod
    def webserver_timeout_validator(cls, value: str) -> Optional[float]:
        """Check validity of `webserver_timeout` field.

        Args:
            value: webserver_timeout value

        Returns:
            int: integer for webserver_timeout configuration

        Raises:
            ValueError: in the case when the value is out of range
        """
        int_value = int(value)
        if 60 <= int_value <= 300:
            return int_value
        raise ValueError("Value out of range.")

    @validator("feature_flags")
    @classmethod
    def feature_flags_validator(cls, value: str) -> Dict[str, bool]:
        """Check validity of `feature_flags` field.

        Args:
            value: feature flags value

        Returns:
            Dict[str, bool]: dictionary of enabled/disabled feature flags

        Raises:
            ValueError: in case the flag is not supported
        """
        ret = {}
        supported_flags = get_supported_feature_flags()
        unsupported_flags = []
        flags = value.split(",") if value else []
        for f in flags:
            name = f.strip(" !").upper()
            if name not in supported_flags:
                unsupported_flags.append(name)
            enabled = not f.strip().startswith("!")
            ret[name] = enabled

        if unsupported_flags:
            raise ValueError(f"{unsupported_flags} flags are not supported.")
        return ret
