#!/usr/bin/env python3
"""Write the CRAB configs of the NPLB campaign from a cluster profile (stdlib only).

Three test sets; the first two for path counts 1 and 4:

* md set (md_set.py), on network configs C0, C2 and C3: phase 0 (ping-pong across and within a
  leaf), phase 1 (victim alone), phase 2 (victim against an aggressor, and alone). Files
  `md_p0_...`, `md_p1_...`, `md_p2_<victim>_...`. C3 gets phase 1 with one path only.
* colleague set (colleague_set.py), on C0 and C2: the three congestion tests of the existing
  Leonardo configs, adapted to this hardware. Files `col_a2a_...`, `col_agtr_...`, `col_ardc_...`.
* ping-pong set (pingpong_set.py), `--set pingpong`, one network config and one path count: the
  bandwidth ceiling of one rank per node for three node pairs and three port variants, and the
  node total of a pairwise x8. It needs `--all-ports PROFILE` (the probe's profile over every
  port, to find a card's second port), writes the node names into each file's `--nodelist`, and
  is the only set that does: `--out` must therefore sit inside a `local/` directory. Files
  `pp_n1n2_...`, `pp_n1n3_pw8_...`; it is not part of `both`.

Every md-set app except the aggressors gets `-grty min(100, ceil(2 MiB / msgsize))`; the
colleague set has none.

`--smoke` writes the same structure with few sizes and iterations for a quick test (files get a
`smoke_` prefix). The profile is the JSON the probe's `--merge` writes:
`{"nodes": [...], "ppn": 8, "nics": [...], "cpus": "0,1,...", "mem_gib": 1800}`; node names are
never written into a config (except by the ping-pong set).

    python3 make_configs.py profile.json --out configs/ [--set md|colleague|both] [--smoke]
    python3 make_configs.py profile8.json --out local/pp --set pingpong
        --all-ports profile16.json --net C2 --paths 1

The set modules build their files through a `Settings`, which carries the options of the run and
the pieces both sets share (apps, chains, allocations, global options).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from colleague_set import COLLEAGUE_NETS, build_colleague_files  # noqa: E402
from md_set import (  # noqa: E402
    AGGRESSOR_KINDS,
    P0_ASSUMPTION,
    build_md_files,
    layout_kinds,
    selected_kinds,
)
from pingpong_set import build_pingpong_files  # noqa: E402

MIB = 1 << 20
GRTY_BYTES = 2 * MIB  # data per timed window the md set aims for
GRTY_MAX = 100  # operations per timed window, at most
SMOKE_SIZES = [8, 32768, 2 * MIB]
BURST_LENGTHS = ("0.001", "0.01", "0.1")
BURST_PAUSES = ("0.000001", "0.0001", "0.01")
SMOKE_BURST = ("0.01", "0.0001")
NETS = ("C0", "C2", "C3")


def grty_for(size: int) -> int:
    """`-grty` for one message size: about 2 MiB or 100 operations per timed window."""
    return min(GRTY_MAX, math.ceil(GRTY_BYTES / size))


@dataclass(frozen=True)
class Layout:
    """How the nodes are split between the victim and the aggressor partitions."""

    nodes: int
    victim_share: int
    aggressor_share: int
    victim_nodes: int  # nodes the victim partition gets (interleaved: S1+S3)


LAYOUTS = {4: Layout(4, 50, 50, 2), 3: Layout(3, 67, 33, 2)}
PAIR_LAYOUT = Layout(2, 50, 50, 1)  # the ping-pong set: two nodes per job, no partitions


@dataclass(frozen=True)
class Placement:
    """The per-app placement keys every app carries."""

    ppn: int
    nics: tuple[str, ...]
    cpus: str


@dataclass(frozen=True)
class Profile:
    ppn: int
    nics: tuple[str, ...]
    cpus: str
    cores: tuple[int, ...]
    mem_gib: float
    layout: Layout
    nodes: tuple[str, ...] = ()  # in the profile's (Slurm) order; only the ping-pong set uses them


@dataclass
class Built:
    """A generated file: its config, and per experiment the sizes dropped for memory."""

    config: dict[str, Any]
    skipped: dict[str, list[str]]


class Collector:
    """The experiments of one file, with the sizes each one dropped."""

    def __init__(self) -> None:
        self.experiments: dict[str, Any] = {}
        self.skipped: dict[str, list[str]] = {}

    def add(
        self,
        name: str,
        text: str,
        apps: dict[str, dict[str, Any]],
        skipped: list[str],
        allocation: dict[str, Any] | None = None,
    ) -> None:
        if not apps:
            self.skipped[name] = [f"experiment dropped, every size skipped: {', '.join(skipped)}"]
            return
        if skipped:
            self.skipped[name] = skipped
            text += f" Skipped sizes (memory): {', '.join(skipped)}."
        experiment: dict[str, Any] = {"description": text}
        if allocation is not None:
            experiment["local_options"] = {"allocation": allocation}
        experiment["apps"] = apps
        self.experiments[name] = experiment

    def built(self, config: dict[str, Any]) -> Built:
        return Built({**config, "experiments": self.experiments}, self.skipped)


@dataclass
class Settings:
    """The options of one run, and the pieces both test sets build their files from."""

    prof: Profile
    smoke: bool
    minruns: int | None
    maxruns: int | None
    walltime: str | None
    timeout: float | None
    iter_small: int
    iter_128m: int
    iter_512m: int
    aggressor_kinds: tuple[str, ...] = AGGRESSOR_KINDS  # phase-2 aggressors to build

    aggressor_size = 2 * MIB  # both sets: the aggressor's message size

    def collector(self) -> Collector:
        """An empty experiment collector for one file."""
        return Collector()

    def placement(self) -> Placement:
        """Every rank of a node: the profile's ppn, NICs and cores."""
        return Placement(self.prof.ppn, self.prof.nics, self.prof.cpus)

    def single_rank_placement(self) -> Placement:
        """One rank per node, on the first NIC and the lowest core (phase 0)."""
        return Placement(1, self.prof.nics[:1], str(min(self.prof.cores)))

    def sizes(self, full: list[int]) -> list[int]:
        """The message sizes of a test: `full`, or the few of a smoke run."""
        return SMOKE_SIZES if self.smoke else full

    def burst_grid(self) -> list[tuple[str, str]]:
        """The (burst length, pause) pairs of the bursty aggressor."""
        if self.smoke:
            return [SMOKE_BURST]
        return [(b, p) for b in BURST_LENGTHS for p in BURST_PAUSES]

    def iterations(self, size: int) -> int:
        """`-iter` for one message size."""
        if self.smoke:
            return 50
        if size >= 512 * MIB:
            return self.iter_512m
        return self.iter_128m if size >= 128 * MIB else self.iter_small

    def partitions(self, mode: str) -> dict[str, Any]:
        """A victim/aggressor allocation (shares in percent)."""
        layout = self.prof.layout
        return {
            "mode": mode,
            "partitions": {
                "victim": {"share": layout.victim_share},
                "aggressor": {"share": layout.aggressor_share},
            },
        }

    @staticmethod
    def app(
        path: str,
        args: str,
        where: Placement,
        paths: int,
        *,
        start: str,
        end: str = "",
        collect: bool = True,
        partition: str | None = None,
        env: dict[str, str] | None = None,
        ucx_env: bool = True,
    ) -> dict[str, Any]:
        """One app entry with the placement keys of the profile.

        An empty `where.nics` leaves the `nics` key out (the app picks its devices through
        `env`); `ucx_env=False` leaves out the campaign's UCX variables.
        """
        entry: dict[str, Any] = {"path": path, "args": args, "collect": collect}
        entry["start"], entry["end"] = start, end
        if partition:
            entry["partition"] = partition
        entry["ppn"] = where.ppn
        if not start.startswith("s"):  # a chained app runs on its head's cores: no cpus allowed
            entry["cpus"] = where.cpus
        if where.nics:
            entry["nics"] = list(where.nics)
        base = {"UCX_MAX_RNDV_RAILS": "1", "UCX_IB_NUM_PATHS": str(paths)} if ucx_env else {}
        if base or env:
            entry["env"] = {**base, **(env or {})}
        return entry

    def chain(
        self,
        path: str,
        prefix: str,
        sizes: list[int],
        where: Placement,
        paths: int,
        *,
        head_start: str,
        partition: str | None,
        memory: tuple[str, int] | None = None,
        grty: bool = False,
        env: dict[str, str] | None = None,
        ucx_env: bool = True,
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """The apps of one test: one per size, each starting after the previous one ends.

        `memory` is (kind, nranks) with kind "pp" (ping-pong/pairwise: 2 x size per rank), "a2a"
        (2 x nranks x size) or "ag" ((nranks + 1) x size): sizes whose buffers on one node pass
        half the node's memory are dropped and returned as text. Only the head carries the
        partition. `grty` adds `-grty` from the size (md set). `env` and `ucx_env` go to every app
        (see `app`).
        """
        apps: dict[str, dict[str, Any]] = {}
        skipped: list[str] = []
        limit = 0.5 * self.prof.mem_gib * 2**30
        for size in sizes:
            if memory is not None:
                kind, nranks = memory
                per_rank = {"pp": 2 * size, "a2a": 2 * nranks * size, "ag": (nranks + 1) * size}
                need = where.ppn * per_rank[kind]
                if need > limit:
                    skipped.append(
                        f"{size} ({need / 2**30:.1f} GiB per node, limit {limit / 2**30:.1f})"
                    )
                    continue
            key = len(apps)
            args = f"{prefix} -msgsize {size} -iter {self.iterations(size)}".strip()
            if grty:
                args += f" -grty {grty_for(size)}"
            apps[str(key)] = self.app(
                path,
                args,
                where,
                paths,
                start=head_start if key == 0 else f"s{key - 1}",
                partition=partition if key == 0 else None,
                env=env,
                ucx_env=ucx_env,
            )
        return apps, skipped

    def config(
        self,
        stem: str,
        net: str,
        kind: str,
        allocation: dict[str, Any] | None,
        *,
        ppn: int | None = None,
        nodelist: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        """A config without experiments.

        `kind` is "md", "colleague" or "pingpong": other run defaults. `ppn` and `nodelist`
        (ping-pong set) set the ranks per node and pin the job to those nodes with `--nodelist`;
        numnodes is then their count.
        """
        if self.smoke:
            minruns, walltime, timeout, factor = 1, "00:15:00", 300.0, 1
        elif kind == "pingpong":
            minruns, walltime, timeout, factor = 5, "02:00:00", 3600.0, 1
        elif kind == "md":
            minruns, walltime, timeout, factor = (20 if net == "C0" else 10), "04:00:00", 3600.0, 2
        else:
            minruns, walltime, timeout, factor = 1, "00:20:00", 1200.0, 1
        minruns = self.minruns if self.minruns is not None else minruns
        maxruns = self.maxruns if self.maxruns is not None else minruns * factor
        opts: dict[str, Any] = {
            "name": stem,
            "numnodes": str(len(nodelist) if nodelist else self.prof.layout.nodes),
            "ppn": str(ppn if ppn is not None else self.prof.ppn),
            "minruns": str(minruns),
            "maxruns": str(maxruns),
            "timeout": str(self.timeout if self.timeout is not None else timeout),
            "outformat": "csv",
            "walltime": self.walltime or walltime,
            "sbatch_directives": ["--exclusive", f"--job-name=nplb_{stem}"],
        }
        if nodelist:
            opts["sbatch_directives"].append(f"--nodelist={','.join(nodelist)}")
        if allocation is not None:
            opts["allocation"] = allocation
        return {"schema_version": 2, "global_options": opts}


def parse_cpus(text: str) -> list[int]:
    """The core numbers of a cpus list such as "0,1,32-33"."""
    cores: list[int] = []
    for part in str(text).split(","):
        low, _, high = part.strip().partition("-")
        try:
            cores += range(int(low), int(high or low) + 1)
        except ValueError:
            raise ValueError(f"cpus: {text!r} is not a core list like 0,1,32-33") from None
    return cores


def load_profile(path: Path, mem_gib: float | None, smoke: bool, pingpong: bool = False) -> Profile:
    """Read and validate a profile; raises ValueError with what is wrong.

    The ping-pong set takes any profile of two or more distinct nodes; the others need 3 or 4.
    """
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read profile {path}: {exc}") from None
    missing = [k for k in ("nodes", "ppn", "nics", "cpus") if k not in data]
    if missing:
        raise ValueError(f"profile lacks: {', '.join(missing)}")
    nics, cores, ppn = tuple(data["nics"]), parse_cpus(data["cpus"]), int(data["ppn"])
    if ppn != len(nics):
        raise ValueError(f"ppn {ppn} must equal the number of nics ({len(nics)})")
    if len(cores) != ppn:
        raise ValueError(f"cpus {data['cpus']!r} has {len(cores)} cores, ppn is {ppn}")
    count = len(data["nodes"])
    if pingpong:
        if count < 2 or len(set(data["nodes"])) != count:
            raise ValueError(f"nodes: {count} listed; the ping-pong set needs 2 or more distinct")
        layout = PAIR_LAYOUT
    elif count in LAYOUTS:
        layout = LAYOUTS[count]
    elif smoke and count > 4:
        layout = LAYOUTS[4]
    else:
        raise ValueError(f"nodes: {count} listed; the sets need 3 or 4 (--smoke accepts 4 or more)")
    gib = mem_gib if mem_gib is not None else float(data.get("mem_gib", 256))
    nodes = tuple(str(n) for n in data["nodes"])
    return Profile(ppn, nics, str(data["cpus"]), tuple(cores), gib, layout, nodes)


def tagged(files: dict[str, Built], tag: str) -> dict[str, Built]:
    """The files with `_<tag>` appended to each stem and to its config name and job name.

    Experiment names stay as they are: naming.py and analyze.py parse them.
    """
    out: dict[str, Built] = {}
    for stem, built in files.items():
        new = f"{stem}_{tag}"
        opts = built.config["global_options"]
        opts["name"] = new
        opts["sbatch_directives"] = [
            f"--job-name=nplb_{new}" if d.startswith("--job-name=") else d
            for d in opts["sbatch_directives"]
        ]
        out[new] = built
    return out


def tag_text(text: str) -> str:
    """argparse type of --tag: letters, digits and `-` (a `_` would split a name field)."""
    if re.fullmatch(r"[A-Za-z0-9-]+", text) is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} must be letters, digits and '-' only (no '_', no spaces)"
        )
    return text


def build_all(
    cfg: Settings,
    sets: str,
    nets: list[str],
    path_counts: list[int],
    tag: str | None = None,
    all_ports: dict[str, Any] | None = None,
) -> dict[str, Built]:
    """Every file to write: {file stem: Built}. `all_ports` is the ping-pong set's profile."""
    prefix = "smoke_" if cfg.smoke else ""
    files: dict[str, Built] = {}
    for net in nets:
        for paths in path_counts:
            if sets == "pingpong":
                assert all_ports is not None
                files.update(build_pingpong_files(cfg, prefix, net, paths, all_ports))
            if sets in ("md", "both"):
                files.update(build_md_files(cfg, prefix, net, paths))
            if sets in ("colleague", "both") and net in COLLEAGUE_NETS:
                files.update(build_colleague_files(cfg, prefix, net, paths))
    return tagged(files, tag) if tag else files


def aggressor_refusal(nodes: int) -> str:
    """Why no selected aggressor fits the layout."""
    if nodes == 3:
        return "--aggressors: the 3-node layout has only the pw aggressor"
    offered = ", ".join(layout_kinds(nodes))
    return f"--aggressors: pw is only for the 3-node layout; this one has {offered}"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Write the NPLB campaign configs from a profile.")
    p.add_argument("profile", type=Path, help="cluster profile JSON")
    p.add_argument("--out", type=Path, required=True, help="directory for the config files")
    p.add_argument(
        "--set",
        dest="sets",
        choices=("md", "colleague", "both", "pingpong"),
        default="both",
        help="test set; pingpong is not part of both and needs --all-ports, one --net and one "
        "--paths value, and an --out inside a local/ directory",
    )
    p.add_argument(
        "--all-ports",
        type=Path,
        help="pingpong set: profile JSON over every port (probe --merge without per-card), "
        "to find the second port of the first card",
    )
    p.add_argument(
        "--net",
        nargs="+",
        choices=NETS,
        default=list(NETS),
        help="network configs (the colleague set only runs on C0 and C2)",
    )
    p.add_argument("--paths", nargs="+", type=int, default=[1, 4], help="UCX_IB_NUM_PATHS values")
    p.add_argument(
        "--smoke", action="store_true", help="few sizes and iterations, for a quick test"
    )
    p.add_argument(
        "--aggressors",
        nargs="+",
        choices=AGGRESSOR_KINDS,
        default=list(AGGRESSOR_KINDS),
        metavar="KIND",
        help="phase-2 aggressors of the md set, any of: a2a, burst (every burst of the grid), "
        "incast, pw (the 3-node layout's only one); default: all",
    )
    p.add_argument(
        "--tag",
        type=tag_text,
        help="appended as _TAG to every file name and config name (not to experiment names), "
        "so runs of different setups, say ppn8 and ppn16, never mix",
    )
    p.add_argument("--minruns", type=int)
    p.add_argument("--maxruns", type=int)
    p.add_argument("--walltime", help="HH:MM:SS")
    p.add_argument("--timeout", type=float, help="seconds")
    p.add_argument("--mem-gib", type=float, help="node memory; default: the profile's, else 256")
    p.add_argument("--iter-small", type=int, default=1000, help="-iter up to 16 MiB")
    p.add_argument("--iter-128m", type=int, default=100, help="-iter at 128 MiB")
    p.add_argument("--iter-512m", type=int, default=25, help="-iter at 512 MiB")
    return p


def check_pingpong_args(ap: argparse.ArgumentParser, args: argparse.Namespace) -> dict[str, Any]:
    """Refuse a bad `--set pingpong` call; return the all-ports profile."""
    if args.all_ports is None:
        ap.error("--all-ports: --set pingpong needs the profile over every port")
    if len(args.net) != 1 or len(args.paths) != 1:
        ap.error("--set pingpong takes exactly one --net value and one --paths value")
    if "local" not in args.out.resolve().parts:
        ap.error(
            "--out: --set pingpong writes node names into the configs, so --out must be inside "
            "a local/ directory (git-ignored)"
        )
    try:
        data = json.loads(args.all_ports.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        ap.error(f"--all-ports: cannot read {args.all_ports}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("per_nic"), dict):
        ap.error(
            f"--all-ports: {args.all_ports} has no per_nic (a profile from the probe's --merge)"
        )
    return data


def main(argv: list[str] | None = None) -> int:
    ap = parser()
    args = ap.parse_args(argv)
    pingpong = args.sets == "pingpong"
    all_ports = check_pingpong_args(ap, args) if pingpong else None
    try:
        prof = load_profile(args.profile, args.mem_gib, args.smoke, pingpong)
    except ValueError as exc:
        ap.error(str(exc))
    cfg = Settings(
        prof,
        args.smoke,
        args.minruns,
        args.maxruns,
        args.walltime,
        args.timeout,
        args.iter_small,
        args.iter_128m,
        args.iter_512m,
        tuple(args.aggressors),
    )
    if args.sets in ("md", "both") and not selected_kinds(cfg):
        ap.error(aggressor_refusal(prof.layout.nodes))
    files = build_all(cfg, args.sets, args.net, args.paths, args.tag, all_ports)
    args.out.mkdir(parents=True, exist_ok=True)
    for stem, built in files.items():
        (args.out / f"{stem}.json").write_text(json.dumps(built.config, indent=2) + "\n")
    layout = (
        f"ping-pong set on {len(prof.nodes)} nodes"
        if pingpong
        else f"{prof.layout.nodes}-node layout"
    )
    print(
        f"Wrote {len(files)} files to {args.out} ({layout}, "
        f"ppn {prof.ppn}, {prof.mem_gib:g} GiB per node):"
    )
    for stem, built in files.items():
        count = len(built.config["experiments"])
        print(f"  {stem}.json: {count} experiment{'s' if count != 1 else ''}")
        for name, sizes in built.skipped.items():
            print(f"    skipped in {name}: {'; '.join(sizes)}")
    if args.tag:
        print(f"Tag: {args.tag} (appended to every file and config name).")
    if args.sets in ("colleague", "both"):
        print(f"Colleague set: {' and '.join(COLLEAGUE_NETS)} only; C3 is left out of it.")
    if args.sets in ("md", "both"):
        print(P0_ASSUMPTION)
        print(f"Phase-2 aggressors: {', '.join(selected_kinds(cfg))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
