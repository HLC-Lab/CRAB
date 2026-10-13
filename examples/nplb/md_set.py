"""The md set of the NPLB campaign: phases 0, 1 and 2, with a 3-node variant (see make_configs.py).

Phase 0 is ping-pong across and within a leaf, phase 1 each victim alone, phase 2 each victim
alone and against an aggressor on interleaved victim/aggressor nodes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from naming import experiment_name

if TYPE_CHECKING:
    from make_configs import Built, Settings

MIB = 1 << 20
MD_SIZES = [1, 8, 64, 512, 4096, 32768, 262144, 2 * MIB, 16 * MIB, 128 * MIB, 512 * MIB]
VICTIM_LEAD = "3"  # the aggressor starts about 3 s before the victim chain
VICTIM_PROGRAMS = {  # key -> (wrapper, memory kind)
    "perm": ("blink/pairwise_nb.py", "pp"),
    "a2a": ("blink/alltoall_nb.py", "a2a"),
    "ag": ("blink/allgather_nb.py", "ag"),
}
P0_ASSUMPTION = (
    "Assumption (p0_local): the same NIC index on the first and second node of the victim "
    "partition reaches the same leaf switch."
)


def build_p0(cfg: Settings, stem: str, net: str, paths: int) -> Built:
    """Ping-pong between two nodes, across the spines and within a leaf."""
    out, where = cfg.collector(), cfg.single_rank_placement()
    cases = (
        (
            "cross",
            cfg.partitions("interleaved"),
            "Ping-pong between two nodes that sit on "
            "different leaf switches (first and third node), one rank per node",
        ),
        (
            "local",
            cfg.partitions("linear"),
            "Ping-pong between two nodes that sit on the same "
            "leaf switch (first and second node), one rank per node",
        ),
    )
    for test, allocation, what in cases:
        apps, skipped = cfg.chain(
            "blink/pingpong_b.py",
            "",
            cfg.sizes(MD_SIZES),
            where,
            paths,
            head_start="0",
            partition="victim",
            memory=("pp", 2),
            grty=True,
        )
        out.add(
            experiment_name("p0", test, net, paths),
            f"{what}, over a size sweep.",
            apps,
            skipped,
            allocation,
        )
    return out.built(cfg.config(stem, net, "md", None))


def build_p1(cfg: Settings, stem: str, net: str, paths: int) -> Built:
    """Each victim alone on all the nodes (on the victim nodes only, for the permutation on 3)."""
    prof = cfg.prof
    out, where = cfg.collector(), cfg.placement()
    layout = prof.layout
    three = layout.nodes == 3
    rules = {
        "perm": "a permutation (rank i sends to rank i plus the offset)",
        "a2a": "an all-to-all",
        "ag": "an all-gather",
    }
    for test, (path, kind) in VICTIM_PROGRAMS.items():
        nodes = layout.victim_nodes if (three and test == "perm") else layout.nodes
        nranks = nodes * prof.ppn
        prefix = f"-mode rot -offset {prof.ppn if three else nranks // 2}" if test == "perm" else ""
        partitioned = three and test == "perm"
        apps, skipped = cfg.chain(
            path,
            prefix,
            cfg.sizes(MD_SIZES),
            where,
            paths,
            head_start="0",
            partition="victim" if partitioned else None,
            memory=(kind, nranks),
            grty=True,
        )
        text = (
            f"{rules[test].capitalize()} over a size sweep, alone, on {nodes} nodes with "
            f"{prof.ppn} ranks each."
        )
        out.add(
            experiment_name("p1", test, net, paths),
            text,
            apps,
            skipped,
            cfg.partitions("interleaved") if partitioned else None,
        )
    return out.built(cfg.config(stem, net, "md", {"mode": "linear"}))


def aggressors(cfg: Settings) -> list[tuple[str, str, str, str, dict[str, str]]]:
    """(key, wrapper, args, what it does, extra env) of each aggressor of phase 2."""
    size = cfg.aggressor_size
    if cfg.prof.layout.nodes == 3:
        offset = max(1, cfg.prof.ppn // 2)
        return [
            (
                "pw",
                "blink/pairwise_nb.py",
                f"-mode rot -offset {offset} -msgsize {size} -endl",
                "a pairwise permutation inside its single node",
                {"UCX_TLS": "rc,self"},
            )
        ]
    found = [
        ("a2a", "blink/alltoall_nb.py", f"-msgsize {size} -endl", "a continuous all-to-all", {})
    ]
    for length, pause in cfg.burst_grid():
        found.append(
            (
                f"burst-{length}-{pause}",
                "blink/alltoall_nb.py",
                f"-msgsize {size} -endl -blength {length} -bpause {pause}",
                f"a bursty all-to-all (bursts of {length} s, {pause} s apart)",
                {},
            )
        )
    found.append(
        ("incast", "blink/incast_nb.py", f"-msgsize {size} -endl", "a continuous incast", {})
    )
    return found


def build_p2(cfg: Settings, stem: str, net: str, paths: int, victim: str) -> Built:
    """One victim alone and against each aggressor, on interleaved victim/aggressor nodes."""
    prof = cfg.prof
    out, where = cfg.collector(), cfg.placement()
    path, kind = VICTIM_PROGRAMS[victim]
    nranks = prof.layout.victim_nodes * prof.ppn
    prefix = f"-mode rot -offset {prof.ppn}" if victim == "perm" else ""
    cases: list[tuple[str, Any]] = [(f"{victim}_alone", None)]
    cases += [(f"{victim}_vs_{agg[0]}", agg) for agg in aggressors(cfg)]
    for test, agg in cases:
        apps, skipped = cfg.chain(
            path,
            prefix,
            cfg.sizes(MD_SIZES),
            where,
            paths,
            head_start="0" if agg is None else VICTIM_LEAD,
            partition="victim",
            memory=(kind, nranks),
            grty=True,
        )
        text = f"Victim ({victim}) over a size sweep on the victim nodes, "
        if agg is None:
            text += "alone: the reference for the runs with an aggressor."
        else:
            apps[str(len(apps))] = cfg.app(
                agg[1],
                agg[2],
                where,
                paths,
                start="0",
                end="f",
                collect=False,
                partition="aggressor",
                env=agg[4],
            )
            text += f"while {agg[3]} runs on the aggressor nodes (it starts first and stops at the end)."
        out.add(experiment_name("p2", test, net, paths), text, apps, skipped)
    return out.built(cfg.config(stem, net, "md", cfg.partitions("interleaved")))


def build_md_files(cfg: Settings, prefix: str, net: str, paths: int) -> dict[str, Built]:
    """The md files of one network config and path count: {file stem: Built}.

    C3 only gets phase 1, and only with one path.
    """
    tag = f"{net}_P{paths}"
    files: dict[str, Built] = {}
    if net == "C3" and paths != 1:
        return files
    if net != "C3":
        stem = f"{prefix}md_p0_{tag}"
        files[stem] = build_p0(cfg, stem, net, paths)
    stem = f"{prefix}md_p1_{tag}"
    files[stem] = build_p1(cfg, stem, net, paths)
    for victim in VICTIM_PROGRAMS if net != "C3" else ():
        stem = f"{prefix}md_p2_{victim}_{tag}"
        files[stem] = build_p2(cfg, stem, net, paths, victim)
    return files
