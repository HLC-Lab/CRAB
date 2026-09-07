"""Plan 086 S1: local library of SbatchMan campaign drafts (store only).

Mirrors test_web_library.py's CRUD coverage. `spec` is held opaque here (no
config-shape validation) since a campaign group's draft intentionally carries
`{var}` sweep placeholders that would fail typed single-config validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from crab.web.errors import InputError, NotFoundError
from crab.web.settings import Settings
from crab.web.store.campaign_library import CampaignLibraryStore, _slug

_SPEC = {
    "configsPath": "",
    "crabRoot": "",
    "system": "",
    "env": [],
    "variables": [{"name": "nodes", "values": ["4", "8"]}],
    "groups": [{"tag": "baseline-{nodes}", "preset": "", "variables": [], "draft": {}}],
}


def _settings(tmp_path: Path) -> Settings:
    return Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data")


def test_slug():
    assert _slug("A2A Baseline vs Noise!") == "a2a-baseline-vs-noise"
    assert _slug("  ") == "campaign"


def test_store_crud_and_uniqueness(tmp_path: Path):
    store = CampaignLibraryStore(_settings(tmp_path))
    assert store.list() == []

    a = store.create("My Campaign", _SPEC)
    assert a.id == "my-campaign"
    assert a.spec["variables"][0]["name"] == "nodes"

    # Same name → unique id, not a clobber.
    b = store.create("My Campaign", _SPEC)
    assert b.id == "my-campaign-2"
    assert {e.id for e in store.list()} == {"my-campaign", "my-campaign-2"}

    # Persistence across store instances.
    assert CampaignLibraryStore(_settings(tmp_path)).get("my-campaign").name == "My Campaign"

    updated = {**_SPEC, "system": "leonardo"}
    store.update("my-campaign", "Renamed", updated)
    got = store.get("my-campaign")
    assert got.name == "Renamed" and got.spec["system"] == "leonardo"

    dup = store.duplicate("my-campaign")
    assert dup.name == "Renamed copy" and dup.id != "my-campaign"
    assert dup.spec == got.spec

    store.delete("my-campaign")
    with pytest.raises(NotFoundError):
        store.get("my-campaign")


def test_invalid_id_rejected(tmp_path: Path):
    store = CampaignLibraryStore(_settings(tmp_path))
    with pytest.raises(InputError):
        store.get("../etc/passwd")


def test_missing_entry_raises_not_found(tmp_path: Path):
    store = CampaignLibraryStore(_settings(tmp_path))
    with pytest.raises(NotFoundError):
        store.get("no-such-campaign")
    with pytest.raises(NotFoundError):
        store.update("no-such-campaign", "x", _SPEC)
    with pytest.raises(NotFoundError):
        store.delete("no-such-campaign")


# --------------------------------------------------------------------------- #
# Location: same user-chosen library dir as the experiment library (ADR-014),
# in a campaigns/ subfolder — never collapsing to the bare library root, so
# campaign files never mix with experiment JSON files in the same flat dir.
# --------------------------------------------------------------------------- #
def test_default_location_is_under_the_data_dir(tmp_path: Path):
    settings = _settings(tmp_path)
    assert settings.campaign_library_dir == tmp_path / "data" / "campaigns"


def test_custom_library_dir_uses_a_campaigns_subfolder(tmp_path: Path):
    lib = tmp_path / "my-configs"
    settings = Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "data", library_dir=lib)
    settings.ensure_dirs()
    store = CampaignLibraryStore(settings)
    entry = store.create("In Custom Dir", _SPEC)
    assert (lib / "campaigns" / f"{entry.id}.json").is_file()
    # Never lands directly in the library root (that's the experiment library's file shape).
    assert not (lib / f"{entry.id}.json").exists()
