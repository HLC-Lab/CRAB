"""Typed response shape for a job's fetched CSV result tree (plan 065).

Mirrors `cli/export.py`'s `collect_result_data` output ({lab: {experiment:
[rows]}}) and `crab_dashboard.html`'s `{"labs": labs}` embedding convention —
one shape serves the live in-app view and the standalone export.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ResultsData(BaseModel):
    labs: dict[str, dict[str, list[dict[str, Any]]]]
