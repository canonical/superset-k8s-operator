#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Superset charm."""
import logging
from enum import Enum
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

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


class RegistrationRole(str, Enum):
    """Enum for the `self-registration-role` field."""

    admin = "Admin"
    alpha = "Alpha"
    gamma = "Gamma"
    public = "Public"
    sql_lab = "sql_lab"


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    external_hostname: str
    tls_secret_name: str
    superset_secret_key: Optional[str]
    admin_password: str
    charm_function: FunctionType
    alerts_attach_reports: bool
    dashboard_cross_filters: bool
    dashboard_rbac: bool
    embeddable_charts: bool
    scheduled_queries: bool
    estimate_query_cost: bool
    enable_template_processing: bool
    alert_reports: bool
    sqlalchemy_pool_size: int
    sqlalchemy_pool_timeout: int
    sqlalchemy_max_overflow: int
    self_registration_role: RegistrationRole
    oauth_admin_email: str
    google_client_id: Optional[str]
    google_client_secret: Optional[str]
    oauth_domain: Optional[str]
    http_proxy: Optional[str]
    https_proxy: Optional[str]
    no_proxy: Optional[str]
    load_examples: bool

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
