"""`nplb pack` (examples/nplb/pack.py): the slot's data in one tarball, with a manifest and checksums.

Everything is built in `tmp_path`: a fake DATA_ROOT, WORK (run logs, smoke folder, reports), CAMPAIGN_DIR
and two tiny git repos for CRAB and Blink. The script runs as a subprocess and through the dispatcher in
a fake checkout; a few tests call `pack.main` in-process to make a write fail. No cluster, no network.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
NPLB = REPO / "examples" / "nplb"
PACK_PY = NPLB / "pack.py"
NOW = "2026-11-12T18:00:00"
RUN_HEADER = "\t".join(
    "n state block layout victim job data_dir submitted status elapsed_s note".split()
)
ENV_VARS = (
    "PYTHONPATH NPLB_WORK PAGE_LINES PAGE_COLS DATA_ROOT WORK CAMPAIGN_DIR NPLB_REPORT_DIR PACK_DIR "
    "PACK_FREE_BYTES CRAB_DIR BLINK_DIR NPLB_LAB"
).split()

sys.path.insert(0, str(NPLB))
import pack  # noqa: E402


def env_for(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    for var in ENV_VARS:
        env.pop(var, None)
    env["DATA_ROOT"] = str(tmp_path / "data")
    env["WORK"] = str(tmp_path / "work")
    env["CAMPAIGN_DIR"] = str(tmp_path / "camp")
    env["NPLB_REPORT_DIR"] = str(tmp_path / "work" / "reports")
    env["PACK_DIR"] = str(tmp_path / "packs")
    env["CRAB_DIR"] = str(tmp_path / "crab_repo")
    env["BLINK_DIR"] = str(tmp_path / "blink_repo")
    env["NPLB_NOW"] = NOW
    env.update(extra)
    return env


def run_pack(
    tmp_path: Path, *args: str, python: str = sys.executable, **env: str
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    return subprocess.run(
        [python, str(PACK_PY), *args],
        env=env_for(tmp_path, **env),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=60,
    )


def git(path: Path, *args: str) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(path), "GIT_CONFIG_GLOBAL": os.devnull}
    cfg = ["-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        ["git", *cfg, *args], cwd=path, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def make_repo(path: Path) -> str:
    """A one-commit git repo; returns its short hash."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "file.txt").write_text("one\n")
    git(path, "add", "file.txt")
    git(path, "commit", "-q", "-m", "one")
    return git(path, "rev-parse", "--short", "HEAD")


def stamp(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").timestamp()


def put(path: Path, text: str = "x\n", when: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if when:
        os.utime(path, (stamp(when), stamp(when)))
    return path


def run_folder(root: Path, name: str, when: str | None = None) -> Path:
    """A data run folder with two files and a sub folder; every mtime is WHEN when given."""
    folder = root / name
    put(folder / "slurm_output.log", f"log of {name}\n", when)
    put(folder / "metadata.csv", "a,b\n1,2\n", when)
    put(folder / "exp1" / "error_app_0.log", "err\n", when)
    if when:
        for path in (folder / "exp1", folder):
            os.utime(path, (stamp(when), stamp(when)))
    return folder


def runs_row(n: int, data_dir: str, submitted: str, status: str = "OK") -> str:
    cells = [str(n), "-", "a2a", "same_leaf", "a2a", "100", data_dir, submitted, status, "60", ""]
    return "\t".join(cells)


def write_runs(path: Path, rows: list[str]) -> None:
    put(path, "\n".join([RUN_HEADER, *rows]) + "\n")


def build_tree(tmp_path: Path) -> dict[str, Path]:
    """The slot: 3 run folders (run_a and run_b in the run log, run_c only by mtime), the work folder
    with the run log, net state, node map, reports and a smoke folder, the campaign folder."""
    data = tmp_path / "data"
    work = tmp_path / "work"
    camp = tmp_path / "camp"
    run_folder(data, "run_a", "2026-11-12T09:30:00")
    run_folder(data, "run_b", "2026-11-12T10:30:00")
    run_folder(data, "run_c", "2026-11-12T11:30:00")
    write_runs(
        work / "campaign_runs.tsv",
        [
            runs_row(1, str(data / "run_a"), "2026-11-12T09:00:00"),
            runs_row(2, str(data / "run_b"), "2026-11-12T10:00:00"),
        ],
    )
    put(work / "net_state.json", '{"nplb": "off"}\n')
    put(work / "node_map.json", '{"nodes": {}}\n')
    put(work / "topology_20261112.tsv", "raw\tlldp\n")
    put(work / "topo_probe.log", "probe screen\n")
    put(work / "reports" / "20261112T100000_run-1.txt", "report one\n")
    write_runs(
        work / "smoke" / "campaign_runs.tsv", [runs_row(1, "-", "2026-11-12T08:30:00", "SKIPPED")]
    )
    put(work / "smoke" / "reports" / "20261112T083000_smoke.txt", "smoke report\n")
    write_runs(
        work / "smoke" / "archive" / "20261111-120000" / "campaign_runs.tsv",
        [runs_row(1, "-", "2026-11-11T12:00:00")],
    )
    put(work / "smoke" / "archive" / "20261111-120000" / "campaign" / "schedule.tsv", "old\n")
    put(camp / "schedule.tsv", "n\ttier\n")
    put(camp / "block_a.json", "{}\n")
    make_repo(tmp_path / "crab_repo")
    make_repo(tmp_path / "blink_repo")
    return {"data": data, "work": work, "camp": camp}


def tar_members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as tar:
        out: dict[str, bytes] = {}
        for member in tar.getmembers():
            handle = tar.extractfile(member)
            if member.isfile() and handle is not None:
                out[member.name] = handle.read()
        return out


def tarballs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "packs").glob("*.tar.gz")) if (tmp_path / "packs").is_dir() else []


def info_of(tmp_path: Path) -> dict:
    (tarball,) = tarballs(tmp_path)
    return json.loads(tar_members(tarball)["pack_info.json"])


def listing(root: Path) -> dict[str, tuple[int, int]]:
    """relative path -> (size, mtime_ns) of everything under ROOT, symlinks not followed."""
    out: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        out[str(path.relative_to(root))] = (stat.st_size, stat.st_mtime_ns)
    return out


def report_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    lines = result.stdout.splitlines()
    assert lines[0] == "#report pack", result.stdout + result.stderr
    return lines[1:]


# --- 1. the happy path ----------------------------------------------------------------------------------


def test_pack_puts_the_slot_into_one_tarball_with_manifest_and_checksums(tmp_path: Path) -> None:
    build_tree(tmp_path)
    result = run_pack(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    (tarball,) = tarballs(tmp_path)
    members = tar_members(tarball)
    for name in (
        "data/run_a/slurm_output.log",
        "data/run_a/exp1/error_app_0.log",
        "data/run_b/metadata.csv",
        "data/run_c/slurm_output.log",
        "work/campaign_runs.tsv",
        "work/net_state.json",
        "work/node_map.json",
        "work/topology_20261112.tsv",
        "work/topo_probe.log",
        "work/reports/20261112T100000_run-1.txt",
        "work/smoke/campaign_runs.tsv",
        "work/smoke/reports/20261112T083000_smoke.txt",
        "work/smoke/archive/20261111-120000/campaign_runs.tsv",
        "work/smoke/archive/20261111-120000/campaign/schedule.tsv",
        "campaign/schedule.tsv",
        "campaign/block_a.json",
        "pack_manifest.txt",
        "pack_info.json",
    ):
        assert name in members, name
    manifest = members["pack_manifest.txt"].decode().splitlines()
    seen = set()
    for line in manifest:
        sha, size, name = line.split("  ", 2)
        assert sha == hashlib.sha256(members[name]).hexdigest()
        assert int(size) == len(members[name])
        seen.add(name)
    assert seen == set(members) - {"pack_manifest.txt", "pack_info.json"}
    info = json.loads(members["pack_info.json"])
    assert set(info) == {"created", "since", "crab_rev", "blink_rev", "files", "bytes", "skipped"}
    assert info["created"] == NOW
    assert info["since"] == "2026-11-11T12:00:00"
    assert info["files"] == len(manifest)
    assert info["bytes"] == sum(len(members[name]) for name in seen)
    assert info["crab_rev"]["hash"] == git(tmp_path / "crab_repo", "rev-parse", "--short", "HEAD")
    assert info["blink_rev"] == {
        "hash": git(tmp_path / "blink_repo", "rev-parse", "--short", "HEAD"),
        "dirty": False,
    }
    sidecar = tarball.with_name(tarball.name + ".sha256")
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    assert sidecar.read_text() == f"{digest}  {tarball.name}\n"
    assert not list((tmp_path / "packs").glob("*.partial"))
    assert [p.name for p in (tmp_path / "packs").iterdir()].count(tarball.name) == 1


def test_blink_rev_is_null_without_the_blink_folder(tmp_path: Path) -> None:
    build_tree(tmp_path)
    shutil.rmtree(tmp_path / "blink_repo")
    assert run_pack(tmp_path).returncode == 0
    assert info_of(tmp_path)["blink_rev"] is None


# --- 2 + 3. --since --------------------------------------------------------------------------------------


def old_folder(tmp_path: Path, name: str = "run_old") -> None:
    run_folder(tmp_path / "data", name, "2026-11-01T12:00:00")


def test_default_since_is_the_earliest_submit_and_old_unreferenced_folders_stay_out(
    tmp_path: Path,
) -> None:
    build_tree(tmp_path)
    old_folder(tmp_path)
    # referenced by a row, but the folder itself is older than the window: always packed
    run_folder(tmp_path / "data", "run_ref_old", "2026-11-01T12:00:00")
    write_runs(
        tmp_path / "work" / "smoke" / "campaign_runs.tsv",
        [runs_row(1, str(tmp_path / "data" / "run_ref_old"), "2026-11-12T08:30:00")],
    )
    result = run_pack(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    names = tar_members(tarballs(tmp_path)[0])
    assert "data/run_ref_old/metadata.csv" in names
    assert not any(n.startswith("data/run_old/") for n in names)
    skipped = info_of(tmp_path)["skipped"]
    assert any("run_old" in str(item) for item in skipped)
    assert not any("run_ref_old" in str(item) for item in skipped)


def test_without_run_log_rows_everything_is_packed_and_since_is_null(tmp_path: Path) -> None:
    build_tree(tmp_path)
    old_folder(tmp_path)
    for log in (tmp_path / "work").rglob("campaign_runs.tsv"):
        write_runs(log, [])
    assert run_pack(tmp_path).returncode == 0
    members = tar_members(tarballs(tmp_path)[0])
    assert "data/run_old/metadata.csv" in members
    info = json.loads(members["pack_info.json"])
    assert info["since"] is None and info["skipped"] == []


@pytest.mark.parametrize("since", ["2026-11-12T08:00", "2026-11-12", "2026-11-12T08:00:00"])
def test_since_flag_narrows_the_same_way(tmp_path: Path, since: str) -> None:
    build_tree(tmp_path)
    old_folder(tmp_path)
    run_folder(tmp_path / "data", "run_new", "2026-11-12T12:00:00")
    for log in (tmp_path / "work").rglob("campaign_runs.tsv"):
        write_runs(log, [])
    assert run_pack(tmp_path, "--since", since).returncode == 0
    members = tar_members(tarballs(tmp_path)[0])
    assert "data/run_new/metadata.csv" in members
    assert "data/run_old/metadata.csv" not in members
    info = json.loads(members["pack_info.json"])
    assert info["since"] == (
        "2026-11-12T00:00:00" if since == "2026-11-12" else "2026-11-12T08:00:00"
    )
    assert any("run_old" in str(item) for item in info["skipped"])


@pytest.mark.parametrize("since", ["yesterday", "12/11/2026", "2026-13-01", "2026-11-12 08:00", ""])
def test_a_bad_since_is_a_usage_error_and_writes_nothing(tmp_path: Path, since: str) -> None:
    build_tree(tmp_path)
    result = run_pack(tmp_path, "--since", since)
    assert result.returncode == 2
    assert since in result.stderr or "--since" in result.stderr
    assert result.stdout == "" and "Traceback" not in result.stderr
    assert not (tmp_path / "packs").exists()


def test_an_unreadable_run_log_packs_everything_and_says_so(tmp_path: Path) -> None:
    build_tree(tmp_path)
    old_folder(tmp_path)
    put(tmp_path / "work" / "smoke" / "campaign_runs.tsv", "not\ta\trun\tlog\n")
    result = run_pack(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    members = tar_members(tarballs(tmp_path)[0])
    assert "data/run_old/metadata.csv" in members
    assert json.loads(members["pack_info.json"])["since"] is None
    assert "smoke/campaign_runs.tsv" in result.stdout


# --- 4. the tarball never contains itself or an earlier one ----------------------------------------------


def test_a_second_pack_does_not_contain_the_first(tmp_path: Path) -> None:
    build_tree(tmp_path)
    assert run_pack(tmp_path).returncode == 0
    assert run_pack(tmp_path).returncode == 0
    first, second = tarballs(tmp_path)
    assert first != second
    assert not any(".tar.gz" in name for name in tar_members(second))


def test_the_pack_dir_inside_the_work_folder_is_excluded(tmp_path: Path) -> None:
    build_tree(tmp_path)
    inside = tmp_path / "work" / "mypacks"
    assert run_pack(tmp_path, "--out", str(inside)).returncode == 0
    assert run_pack(tmp_path, "--out", str(inside)).returncode == 0
    second = sorted(inside.glob("*.tar.gz"))[-1]
    names = tar_members(second)
    assert not any("mypacks" in n or ".tar.gz" in n for n in names)
    assert "work/net_state.json" in names


def test_pycache_and_pyc_files_are_left_out(tmp_path: Path) -> None:
    build_tree(tmp_path)
    put(tmp_path / "work" / "__pycache__" / "x.cpython-310.pyc", "b")
    put(tmp_path / "camp" / "old.pyc", "b")
    assert run_pack(tmp_path).returncode == 0
    names = tar_members(tarballs(tmp_path)[0])
    assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)


def test_out_is_created_and_must_be_a_directory(tmp_path: Path) -> None:
    build_tree(tmp_path)
    target = tmp_path / "deep" / "er" / "out"
    assert run_pack(tmp_path, "--out", str(target)).returncode == 0
    assert len(list(target.glob("*.tar.gz"))) == 1
    afile = put(tmp_path / "afile")
    bad = run_pack(tmp_path, "--out", str(afile))
    assert bad.returncode == 2 and str(afile) in bad.stderr


# --- 5. disk check, dry run, partial files ---------------------------------------------------------------


def test_not_enough_free_space_fails_with_no_tarball_and_no_partial(tmp_path: Path) -> None:
    build_tree(tmp_path)
    result = run_pack(tmp_path, PACK_FREE_BYTES="100")
    assert result.returncode == 1
    lines = report_lines(result)
    assert lines[0].startswith("VERDICT: FAIL pack: need ")
    assert "free 100 B" in lines[0] or "free 100 B" in " ".join(lines[:2])
    assert str(tmp_path / "packs") in " ".join(lines[:6])
    packs = tmp_path / "packs"
    assert not packs.exists() or list(packs.iterdir()) == []


def test_the_disk_check_wants_5_percent_over_the_uncompressed_size(tmp_path: Path) -> None:
    build_tree(tmp_path)
    dry = run_pack(tmp_path, "--dry-run")
    total = int(
        next(line for line in dry.stdout.splitlines() if line.startswith("bytes")).split()[1]
    )
    need = -(-total * 105 // 100)
    assert run_pack(tmp_path, PACK_FREE_BYTES=str(need - 1)).returncode == 1
    assert run_pack(tmp_path, PACK_FREE_BYTES=str(need)).returncode == 0


def test_dry_run_reports_and_writes_nothing(tmp_path: Path) -> None:
    build_tree(tmp_path)
    old_folder(tmp_path)
    before = {name: listing(tmp_path / name) for name in ("data", "work", "camp")}
    result = run_pack(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = report_lines(result)
    assert lines[0].startswith("VERDICT: DRY RUN pack: ")
    text = "\n".join(lines)
    assert "files" in text and "free" in text and "run_old" in text
    assert {name: listing(tmp_path / name) for name in ("data", "work", "camp")} == before
    assert not (tmp_path / "packs").exists()


def test_a_failure_while_writing_removes_the_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    build_tree(tmp_path)
    for key, value in env_for(tmp_path).items():
        monkeypatch.setenv(key, value)
    calls = {"n": 0}
    real = pack.add_file

    def flaky(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk went away")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pack, "add_file", flaky)
    code = pack.main([])
    out = capsys.readouterr()
    assert code == 1
    assert "VERDICT: FAIL pack:" in out.out and "disk went away" in out.out
    assert list((tmp_path / "packs").iterdir()) == []


# --- 6. sources are never touched; symlinks are skipped --------------------------------------------------


def test_sources_are_not_modified_and_symlinks_are_skipped(tmp_path: Path) -> None:
    tree = build_tree(tmp_path)
    secret = put(tmp_path / "outside" / "secret.txt", "do not pack\n")
    (tree["work"] / "link_file").symlink_to(secret)
    (tree["work"] / "link_dir").symlink_to(secret.parent)
    (tree["data"] / "run_link").symlink_to(tree["data"] / "run_a")
    before = {name: listing(tree[name]) for name in tree}
    result = run_pack(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {name: listing(tree[name]) for name in tree} == before
    members = tar_members(tarballs(tmp_path)[0])
    assert not any("link_" in n or "run_link" in n or "secret" in n for n in members)
    skipped = json.loads(members["pack_info.json"])["skipped"]
    text = json.dumps(skipped)
    assert "link_file" in text and "link_dir" in text and "run_link" in text
    assert "link_file" in result.stdout and "run_link" in result.stdout


# --- 7. the report ---------------------------------------------------------------------------------------


def test_the_report_has_the_verdict_first_and_the_copy_out_lines(tmp_path: Path) -> None:
    build_tree(tmp_path)
    result = run_pack(tmp_path)
    lines = report_lines(result)
    (tarball,) = tarballs(tmp_path)
    info = info_of(tmp_path)
    assert lines[0].startswith(f"VERDICT: OK pack: {info['files']} files, ")
    assert f"-> {tarball.name} (" in lines[0] and lines[0].endswith("MiB)")
    text = "\n".join(lines)
    digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
    assert str(tarball.resolve()) in text and digest in text
    assert f"{tarball.stat().st_size} bytes" in text
    assert f"scp <lab-host>:{tarball.resolve()} ." in text
    assert f"sha256sum -c {tarball.name}.sha256" in text
    assert info["crab_rev"]["hash"] in text and info["blink_rev"]["hash"] in text
    assert len(lines) <= 38 and max(len(x) for x in lines if str(tmp_path) not in x) <= 100


def test_a_long_skipped_list_is_cut_to_one_page(tmp_path: Path) -> None:
    tree = build_tree(tmp_path)
    for index in range(40):
        (tree["work"] / f"link_{index:02d}").symlink_to(tmp_path / "outside")
        old_folder(tmp_path, f"run_old_{index:02d}")
    result = run_pack(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = report_lines(result)
    assert len(lines) <= 38 and max(len(x) for x in lines if str(tmp_path) not in x) <= 100
    assert any(line.strip().startswith("... ") and line.strip().endswith(" more") for line in lines)
    assert "VERDICT: OK" in lines[0]
    assert len(info_of(tmp_path)["skipped"]) == 80


def test_narration_goes_to_stderr(tmp_path: Path) -> None:
    build_tree(tmp_path)
    result = run_pack(tmp_path)
    narration = [x for x in result.stderr.splitlines() if x.startswith("[18:00:00] ")]
    assert narration, result.stderr
    assert "[18:" not in result.stdout


# --- 9. an empty tree ------------------------------------------------------------------------------------


def test_an_empty_tree_is_a_failure_with_no_tarball(tmp_path: Path) -> None:
    result = run_pack(tmp_path)
    assert result.returncode == 1
    lines = report_lines(result)
    assert lines[0].startswith("VERDICT: FAIL pack: nothing to pack (checked ")
    assert str(tmp_path / "data") in " ".join(lines[:6])
    assert str(tmp_path / "work") in " ".join(lines[:6])
    assert not (tmp_path / "packs").exists()


# --- 8. the dispatcher -----------------------------------------------------------------------------------


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    chk = tmp_path / "chk"
    nplb_dir = chk / "examples" / "nplb"
    (nplb_dir / "lib").mkdir(parents=True)
    for path in [NPLB / "nplb", NPLB / "lib" / "pages.sh", *NPLB.glob("*.py")]:
        shutil.copy(path, nplb_dir / path.relative_to(NPLB))
    (nplb_dir / "nplb").chmod(0o755)
    (tmp_path / "elsewhere").mkdir()
    return chk


def run_nplb(tmp_path: Path, chk: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(chk / "examples" / "nplb" / "nplb"), *args],
        env=env_for(tmp_path, NPLB_REPORT_DIR=str(tmp_path / "reports"), **env),
        cwd=tmp_path / "elsewhere",
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_verb_pages_and_saves_the_report_as_pack(tmp_path: Path, checkout: Path) -> None:
    build_tree(tmp_path)
    result = run_nplb(tmp_path, checkout, "pack")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].startswith("pack | page 1/")
    assert lines[1].startswith("VERDICT: OK pack: ")
    saved = sorted((tmp_path / "reports").glob("*_pack.txt"))
    assert len(saved) == 1
    assert saved[0].read_text(encoding="utf-8").startswith("VERDICT: OK pack: ")
    assert len(tarballs(tmp_path)) == 1


def test_the_verb_passes_the_failure_exit_code_and_pages_it(tmp_path: Path, checkout: Path) -> None:
    build_tree(tmp_path)
    failed = run_nplb(tmp_path, checkout, "pack", PACK_FREE_BYTES="10")
    assert failed.returncode == 1
    assert "VERDICT: FAIL pack: need " in failed.stdout
    assert tarballs(tmp_path) == []


def test_an_unknown_flag_is_a_usage_error(tmp_path: Path, checkout: Path) -> None:
    build_tree(tmp_path)
    result = run_nplb(tmp_path, checkout, "pack", "--bogus")
    assert result.returncode == 2
    assert "usage" in result.stderr.lower() and "Traceback" not in result.stderr
    assert not (tmp_path / "reports").exists() and tarballs(tmp_path) == []


def test_help_lists_pack_in_one_line(tmp_path: Path, checkout: Path) -> None:
    helped = run_nplb(tmp_path, checkout, "help")
    lines = helped.stdout.splitlines()
    assert len(lines) <= 31 and max(len(line) for line in lines) <= 100
    pack_lines = [x for x in lines if x.strip().startswith("pack ")]
    assert len(pack_lines) == 1
    assert "[--since T] [--out DIR] [--dry-run]" in pack_lines[0]


# --- 10. python 3.10 -------------------------------------------------------------------------------------


def test_dry_run_works_under_python_3_10(tmp_path: Path) -> None:
    python310 = shutil.which("python3.10")
    if python310 is None:
        pytest.skip("no python3.10 here")
    build_tree(tmp_path)
    result = run_pack(tmp_path, "--dry-run", python=python310)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report_lines(result)[0].startswith("VERDICT: DRY RUN pack: ")
