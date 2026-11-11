"""examples/nplb/lab_start.sh against a fake git world: a bare origin, a clone of it, a stub dispatcher.

The script is committed into the fake origin (so it is a tracked file of the clone, the way it is in the
real checkout) and run from there with bash. `NPLB_BIN` points at a stub that logs its calls to
$STUB_LOG, prints a fixed line plus the marker variable `local/nplb_env.sh` exports, and exits 1 for the
verb named in $STUB_FAIL. Nothing here touches the real repo's git state, the network or a cluster.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "examples" / "nplb" / "lab_start.sh"
RELATIVE = "examples/nplb/lab_start.sh"

STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
echo "stub ran: $* marker=${LAB_MARK:-unset}"
[ "$*" = "${STUB_FAIL:-}" ] && exit 1
exit 0
"""

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    done = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@dataclass
class World:
    root: Path
    origin: Path
    seed: Path
    lab: Path
    stub: Path
    log: Path

    def push_commits(self, n: int) -> None:
        """n new commits on origin/nplb, made in the seed clone."""
        for i in range(n):
            (self.seed / f"news{i}.txt").write_text(f"news {i}\n")
            git(self.seed, "add", f"news{i}.txt")
            git(self.seed, "commit", "-m", f"news {i}")
        git(self.seed, "push", "origin", "nplb")

    def run(self, **env: str) -> subprocess.CompletedProcess[str]:
        full = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "STUB_FAIL")}
        full.update(
            NPLB_BIN=str(self.stub), STUB_LOG=str(self.log), GIT_TERMINAL_PROMPT="0", **GIT_ENV
        )
        full.update(env)
        return subprocess.run(
            ["bash", str(self.lab / RELATIVE)],
            cwd=self.root,
            env=full,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []


@pytest.fixture
def world(tmp_path: Path) -> World:
    root = tmp_path / "world"
    root.mkdir()
    origin = root / "origin.git"
    seed = root / "seed"
    lab = root / "lab"
    git(root, "init", "--bare", "-b", "nplb", str(origin))
    git(root, "init", "-b", "nplb", str(seed))
    git(seed, "remote", "add", "origin", str(origin))
    (seed / "examples" / "nplb").mkdir(parents=True)
    shutil.copy(SCRIPT, seed / RELATIVE)
    (seed / "README.txt").write_text("hello\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-m", "first")
    git(seed, "push", "origin", "nplb")
    git(root, "clone", "-b", "nplb", str(origin), str(lab))
    stub = root / "stub_nplb"
    stub.write_text(STUB)
    stub.chmod(0o755)
    return World(root, origin, seed, lab, stub, root / "stub.log")


def summary_page(stdout: str) -> list[str]:
    """The summary page: from the last VERDICT line (its first line) to the end."""
    lines = stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("VERDICT:")]
    assert starts, f"no VERDICT line in:\n{stdout}"
    return lines[starts[-1] :]


def assert_page_fits(stdout: str) -> None:
    page = summary_page(stdout)
    assert len(page) <= 38, page
    assert all(len(line) <= 100 for line in page), page


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def test_behind_origin_pulls_and_runs_the_three_verbs(world: World) -> None:
    world.push_commits(2)
    before = head(world.lab)
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert head(world.lab) == git(world.origin, "rev-parse", "nplb")
    assert head(world.lab) != before
    assert re.search(r"\b2 commits\b", result.stdout), result.stdout
    assert world.calls() == ["update", "build check", "node check"]
    page = summary_page(result.stdout)
    assert page[0].startswith("VERDICT: OK lab_start: branch nplb at ")
    assert git(world.lab, "rev-parse", "--short", "HEAD") in page[0]
    assert_page_fits(result.stdout)


def test_level_with_origin_does_not_pull(world: World) -> None:
    before = head(world.lab)
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert head(world.lab) == before
    assert not re.search(r"brought \d+ commit", result.stdout), result.stdout
    assert world.calls() == ["update", "build check", "node check"]
    assert summary_page(result.stdout)[0].startswith("VERDICT: OK lab_start")
    assert_page_fits(result.stdout)


def test_ahead_of_origin_is_said_and_not_fatal(world: World) -> None:
    (world.lab / "mine.txt").write_text("mine\n")
    git(world.lab, "add", "mine.txt")
    git(world.lab, "commit", "-m", "mine")
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"ahead of origin/nplb by 1", result.stdout), result.stdout
    assert world.calls() == ["update", "build check", "node check"]
    assert_page_fits(result.stdout)


def test_wrong_branch_stops_and_changes_nothing(world: World) -> None:
    git(world.lab, "checkout", "-b", "main")
    world.push_commits(1)
    before = head(world.lab)
    result = world.run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "git checkout nplb" in result.stdout
    assert "main" in summary_page(result.stdout)[0]
    assert summary_page(result.stdout)[0].startswith("VERDICT: FAIL lab_start")
    assert world.calls() == []
    assert head(world.lab) == before
    assert git(world.lab, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(world.lab, "status", "--short", "--untracked-files=no") == ""
    assert "next:" not in result.stdout
    assert_page_fits(result.stdout)


def test_local_changes_stop_without_pulling(world: World) -> None:
    world.push_commits(1)
    before = head(world.lab)
    (world.lab / "README.txt").write_text("edited by hand\n")
    result = world.run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "README.txt" in result.stdout
    assert "FAIL local changes: send me this screen" in result.stdout
    assert world.calls() == []
    assert head(world.lab) == before
    assert (world.lab / "README.txt").read_text() == "edited by hand\n"
    assert "next:" not in result.stdout
    assert_page_fits(result.stdout)


def test_unreachable_origin_fails_at_fetch(world: World) -> None:
    git(world.lab, "remote", "set-url", "origin", str(world.root / "missing.git"))
    result = world.run()
    assert result.returncode == 1, result.stdout + result.stderr
    assert "FAIL fetch:" in result.stdout
    assert "if it hangs, the proxy is not set: check local/nplb_env.sh" in result.stdout
    assert summary_page(result.stdout)[0].startswith("VERDICT: FAIL lab_start: fetch")
    assert world.calls() == []
    assert "next:" not in result.stdout
    assert_page_fits(result.stdout)


def test_a_failing_verb_does_not_stop_the_later_ones(world: World) -> None:
    result = world.run(STUB_FAIL="node check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert world.calls() == ["update", "build check", "node check"]
    page = summary_page(result.stdout)
    assert page[0].startswith("VERDICT: FAIL lab_start: node check")
    assert any(line.startswith("OK") and "update" in line for line in page)
    assert any(line.startswith("FAIL") and "node check" in line for line in page)
    assert "next:" not in result.stdout
    assert_page_fits(result.stdout)


def test_first_failing_step_is_named_when_two_fail(world: World) -> None:
    result = world.run(STUB_FAIL="update")
    assert result.returncode == 1
    assert world.calls() == ["update", "build check", "node check"]
    assert summary_page(result.stdout)[0].startswith("VERDICT: FAIL lab_start: update")


def test_streams_and_site_file(world: World) -> None:
    (world.lab / "local").mkdir()
    (world.lab / "local" / "nplb_env.sh").write_text("export LAB_MARK=proxy-set\n")
    world.push_commits(1)
    result = world.run(PYTHONPATH="/nonexistent/old/argparse")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "stub ran: update marker=proxy-set" in result.stdout
    assert re.search(r"^\[\d\d:\d\d:\d\d\] step 1/6: ", result.stderr, re.M), result.stderr
    assert re.search(r"^\[\d\d:\d\d:\d\d\] step 6/6: ", result.stderr, re.M), result.stderr
    assert "step 1/6" not in result.stdout
    assert "VERDICT:" not in result.stderr
    assert_page_fits(result.stdout)


def test_next_lines_end_the_ok_case(world: World) -> None:
    result = world.run()
    assert result.returncode == 0
    assert result.stdout.splitlines()[-2:] == [
        "next: . examples/nplb/env.sh",
        "next: nplb build blink",
    ]


def test_works_from_any_directory_and_never_prompts_without_a_terminal(world: World) -> None:
    # world.run() starts the script in the parent folder of the checkout with stdin closed: it must
    # finish (no `read`) and find the checkout by its own path.
    result = world.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Enter for the next page" not in result.stdout + result.stderr
