"""Typed response shape for a job's fetched CSV result tree (plan 065).

Mirrors `cli/export.py`'s `collect_result_data` output ({experiment: {app:
[rows]}}). `crab_dashboard.html`'s standalone export keeps its own legacy
`{"labs": ...}` embedding convention, out of scope for this shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ResultsData(BaseModel):
    experiments: dict[str, dict[str, list[dict[str, Any]]]]
