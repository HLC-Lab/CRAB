"""Pydantic models describing shapes the backend owns."""

from crab.web.models.config import CrabConfigModel, validate_config

__all__ = ["CrabConfigModel", "validate_config"]
