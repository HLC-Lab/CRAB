"""`crab web` on a port that is already taken prints the documented hint and exits 1.

uvicorn answers a failed bind with `sys.exit(3)` and its own log line, so run.py's `except OSError`
never sees it. run.py therefore tries the bind itself, before it prints the banner. The test holds
a real socket on a free loopback port; no server starts.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest

from crab.web import run
from crab.web.settings import Settings


def _no_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Any]:
    """Point the dashboard at a scratch folder and make any attempt to serve fail loudly."""
    served: list[Any] = []
    monkeypatch.setattr(
        run, "get_settings", lambda: Settings(config_dir=tmp_path / "cfg", data_dir=tmp_path / "d")
    )
    monkeypatch.setattr("uvicorn.run", lambda *a, **kw: served.append((a, kw)))
    return served


def test_taken_port_prints_hint_and_exits_1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    served = _no_server(monkeypatch, tmp_path)
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen()
        port = holder.getsockname()[1]
        with pytest.raises(SystemExit) as exc:
            run.run_server("127.0.0.1", port, open_browser=False)
    out = capsys.readouterr().out
    assert exc.value.code == 1
    assert f"[ERROR] Could not start the server on 127.0.0.1:{port}" in out
    assert "Try: crab web --port <other>" in out
    assert "CRAB web dashboard →" not in out  # no banner for a server that never started
    assert served == []


def test_free_port_reaches_uvicorn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    served = _no_server(monkeypatch, tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    run.run_server("127.0.0.1", port, open_browser=False)
    assert len(served) == 1
    assert f"CRAB web dashboard → http://127.0.0.1:{port}" in capsys.readouterr().out
