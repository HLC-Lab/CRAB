"""Local copies of composed SbatchMan campaign YAML files.

Each write is one timestamped ``.yaml`` file in ``settings.sbatchman_dir``, kept
for the user's own reference/debugging. There is no CRUD beyond writing: the
campaign-authoring state (groups, variables) lives only in the browser's Pinia
store, so this is just an on-disk copy of what was pushed to the cluster —
unlike ``store/library.py``'s CRAB configs, it isn't read back by the app.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from crab.web.settings import Settings, get_settings

_NON_SLUG = re.compile(r"[^a-z0-9]+")

# A longer name would make a file name the filesystem refuses (255 bytes on most).
MAX_SLUG_LENGTH = 60


def campaign_slug(name: str) -> str:
    """The file-name form of a campaign name, shared by the local copy and the staged file."""
    s = _NON_SLUG.sub("-", name.strip().lower()).strip("-")[:MAX_SLUG_LENGTH].rstrip("-")
    return s or "campaign"


def save_campaign_yaml(yaml_text: str, name: str, settings: Settings | None = None) -> Path:
    """Write ``yaml_text`` to a new timestamped file; return its local path."""
    s = settings or get_settings()
    s.sbatchman_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = s.sbatchman_dir / f"{stamp}-{campaign_slug(name)}.yaml"
    path.write_text(yaml_text)
    return path
