#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the Kafka charm."""
import logging
from typing import Optional

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator
from enum import Enum

logger = logging.getLogger(__name__)


class BaseEnumStr(str, Enum):
    """Base class for string enum."""

    def __str__(self) -> str:
        """Return the value as a string."""
        return str(self.value)


class FunctionType(BaseEnumStr):
    """Enum for the `charm-function` field."""

    APP_GUNICORN = "app-gunicorn"
    APP = "app"
    WORKER = "worker"
    BEAT = "beat"


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

    @validator("*", pre=True)
    @classmethod
    def blank_string(cls, value):
        """Check for empty strings."""
        if value == "":
            return None
        return value

    @validator("sqlalchemy_pool_size")
    @classmethod
    def sqlalchemy_pool_size_validator(cls, value: str) -> Optional[int]:
        """Check validity of `sqlalchemy_pool_size` field."""
        int_value = int(value)
        if int_value >= 0 and int_value <= 300:
            return int_value
        raise ValueError("Value out of range.")
