"""The NPLB chart script (examples/nplb/plot.py): data preparation and the missing-matplotlib exit.

matplotlib is not a CRAB dependency, so these tests never draw: they check the pure functions that
turn a summary table into chart specs, and that main() stops early when matplotlib is absent.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

NPLB = Path(__file__).resolve().parents[1] / "examples" / "nplb"


def _load(name: str) -> ModuleType:
    """Import a script from examples/nplb (not a package) under its own file name."""
    if str(NPLB) not in sys.path:
        sys.path.insert(0, str(NPLB))
    spec = importlib.util.spec_from_file_location(name, NPLB / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


analyze = _load("analyze")
plot = _load("plot")

NAN = float("nan")
KIB = 1024
MIB = 1024 * 1024


def make_row(**fields: Any) -> dict[str, Any]:
    """One summary row (all analyze.COLUMNS); defaults describe md p1 perm on C0, one path."""
    row: dict[str, Any] = dict.fromkeys(analyze.COLUMNS, NAN)
    row.update(
        data_dir="d1",
        file="md_p1_perm_C0_P1",
        set="md",
        smoke=False,
        phase="p1",
        victim="perm",
        aggressor=None,
        net="C0",
        paths=1,
        msg_size=8,
        bw_gbps=1.0,
        spread_med=1.5,
        spread_p99=2.0,
    )
    row.update(fields)
    return row


def frame(*rows: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=analyze.COLUMNS)


def prepared(*rows: dict[str, Any]) -> pd.DataFrame:
    return plot.prepare(frame(*rows))[0]


def by_file(charts: list[Any]) -> dict[str, Any]:
    return {c.file: c for c in charts}


def test_size_ticks_are_labelled_with_units() -> None:
    sizes = [1, 8, 64, 512, 4 * KIB, 32 * KIB, 256 * KIB, 2 * MIB, 16 * MIB, 128 * MIB, 512 * MIB]
    labels = [label for _, label in plot.size_ticks(sizes)]
    assert labels == [
        "1 B",
        "8 B",
        "64 B",
        "512 B",
        "4 KiB",
        "32 KiB",
        "256 KiB",
        "2 MiB",
        "16 MiB",
        "128 MiB",
        "512 MiB",
    ]
    assert [pos for pos, _ in plot.size_ticks(sizes)] == sizes


def test_size_ticks_keep_the_sizes_present_and_fall_back_to_all_of_them() -> None:
    assert [lab for _, lab in plot.size_ticks([8, 100, 32 * KIB])] == ["8 B", "32 KiB"]
    assert plot.size_ticks([100, 300]) == [(100, "100 B"), (300, "300 B")]


def test_path_count_wording() -> None:
    assert (plot.path_count(1), plot.path_count(4)) == ("1 path", "4 paths")


def test_pretty_aggressor_names() -> None:
    assert plot.pretty_aggressor("burst-0.01-0.0001") == "burst 0.01 s on, 0.0001 s off"
    assert plot.pretty_aggressor("incast") == "incast"


def test_bandwidth_and_spread_charts_per_group_with_file_names() -> None:
    df = prepared(
        make_row(phase="p0", victim="cross", msg_size=8),
        make_row(phase="p1", msg_size=8),
        make_row(phase="p1", msg_size=8, paths=4),
        make_row(phase="p2", msg_size=8, aggressor=None),
        make_row(phase="p2", msg_size=8, aggressor="a2a", slowdown_med=2.0, slowdown_p99=3.0),
    )
    charts, notes = plot.build_charts(df, {"bw", "spread"})
    assert notes == []
    expected = [
        f"{kind}_md_{phase}_{victim}_alone_P{paths}.png"
        for kind in ("bw", "spread")
        for phase, victim, paths in (
            ("p0", "cross", 1),
            ("p1", "perm", 1),
            ("p1", "perm", 4),
            ("p2", "perm", 1),
        )
    ]
    assert sorted(by_file(charts)) == sorted(expected)
    # the p2 row with an aggressor belongs to the slowdown chart only


def test_smoke_files_get_a_prefix() -> None:
    df = prepared(make_row(smoke=True), make_row(smoke=False))
    charts, _ = plot.build_charts(df, {"bw"})
    assert sorted(by_file(charts)) == [
        "bw_md_p1_perm_alone_P1.png",
        "smoke_bw_md_p1_perm_alone_P1.png",
    ]
    assert "(smoke)" in by_file(charts)["smoke_bw_md_p1_perm_alone_P1.png"].title


def test_only_selects_chart_kinds() -> None:
    df = prepared(make_row())
    assert [c.kind for c in plot.build_charts(df, {"spread"})[0]] == ["spread"]
    assert plot.build_charts(df, {"slowdown"})[0] == []


def test_series_follow_net_order_with_fixed_colors_and_markers() -> None:
    df = prepared(
        make_row(net="C3", msg_size=8, bw_gbps=3.0),
        make_row(net="C2", msg_size=8, bw_gbps=2.0),
        make_row(net="C0", msg_size=8, bw_gbps=1.0),
        make_row(net="X9", msg_size=8, bw_gbps=9.0),
    )
    (chart,) = plot.build_charts(df, {"bw"})[0]
    (panel,) = chart.panels
    assert [s.net for s in panel.series] == ["C0", "C2", "C3", "X9"]
    assert [s.color for s in panel.series] == ["#2a78d6", "#eb6834", "#1baf7a", "#6f6e69"]
    assert [s.marker for s in panel.series] == ["o", "s", "^", "x"]
    assert [s.label for s in panel.series][:2] == ["C0 (ECMP)", "C2 (NPLB)"]
    assert panel.series[0].y == [1.0] and panel.series[0].y_p99 is None


def test_series_values_are_sorted_by_size_and_keep_nan_gaps() -> None:
    df = prepared(
        make_row(msg_size=64, spread_med=NAN, spread_p99=NAN, bw_gbps=3.0),
        make_row(msg_size=8, spread_med=1.5, spread_p99=2.0, bw_gbps=1.0),
        make_row(msg_size=512, spread_med=1.6, spread_p99=2.1, bw_gbps=4.0),
    )
    charts, _ = plot.build_charts(df, {"bw", "spread"})
    spread = by_file(charts)["spread_md_p1_perm_alone_P1.png"].panels[0].series[0]
    assert spread.x == [8, 64, 512]
    assert spread.y[0] == 1.5 and math.isnan(spread.y[1]) and spread.y[2] == 1.6
    assert spread.y_p99 is not None
    assert spread.y_p99[0] == 2.0 and math.isnan(spread.y_p99[1])
    bw = by_file(charts)["bw_md_p1_perm_alone_P1.png"].panels[0].series[0]
    assert bw.y == [1.0, 3.0, 4.0]
    assert by_file(charts)["bw_md_p1_perm_alone_P1.png"].ticks == [
        (8, "8 B"),
        (64, "64 B"),
        (512, "512 B"),
    ]


def test_axes_and_titles() -> None:
    df = prepared(make_row(phase="p1", victim="perm"))
    charts, _ = plot.build_charts(df, {"bw", "spread"})
    bw, spread = by_file(charts).values()
    assert bw.title == "Pairwise rotation, alone, 1 path: bandwidth per rank"
    assert bw.ylabel == "bandwidth per rank (Gb/s)" and bw.yscale == "log"
    assert spread.ylabel == "max/min time across ranks" and spread.yscale == "linear"
    assert spread.reference == 1.0 and bw.reference is None


def test_group_without_values_is_skipped_with_a_note() -> None:
    df = prepared(
        make_row(msg_size=8, spread_med=NAN, spread_p99=NAN, bw_gbps=1.0),
        make_row(msg_size=64, spread_med=NAN, spread_p99=NAN, bw_gbps=2.0),
    )
    charts, notes = plot.build_charts(df, {"bw", "spread"})
    assert [c.kind for c in charts] == ["bw"]
    assert len(notes) == 1 and "spread_md_p1_perm_alone_P1.png" in notes[0]


def test_colleague_set_has_a_chart_per_burst_setting() -> None:
    burst = "burst-0.01-0.0001"
    df = prepared(
        make_row(set="col", phase="cong", victim="a2a", aggressor=burst, net="C0"),
        make_row(set="col", phase="cong", victim="a2a", aggressor=burst, net="C2"),
        make_row(set="col", phase="cong", victim="a2a", aggressor="burst-0.1-0.001", net="C0"),
    )
    charts, _ = plot.build_charts(df, {"bw", "slowdown"})
    files = by_file(charts)
    assert sorted(files) == [
        "bw_col_cong_a2a_burst-0.01-0.0001_P1.png",
        "bw_col_cong_a2a_burst-0.1-0.001_P1.png",
    ]
    chart = files["bw_col_cong_a2a_burst-0.01-0.0001_P1.png"]
    assert [s.net for s in chart.panels[0].series] == ["C0", "C2"]
    assert (
        chart.title
        == "All-to-all, against burst 0.01 s on, 0.0001 s off, 1 path: bandwidth per rank"
    )


def test_slowdown_panels_per_aggressor() -> None:
    def vs(aggressor: str, net: str, size: int, med: float, p99: float) -> dict[str, Any]:
        return make_row(
            phase="p2",
            aggressor=aggressor,
            net=net,
            msg_size=size,
            slowdown_med=med,
            slowdown_p99=p99,
        )

    df = prepared(
        vs("incast", "C0", 8, 3.0, 4.0),
        vs("a2a", "C0", 8, 2.0, 2.5),
        vs("a2a", "C2", 8, 1.2, 1.5),
        vs("a2a", "C2", 64, NAN, NAN),
        vs("burst-0.01-0.0001", "C2", 8, 1.1, 1.3),
        vs("pw", "C0", 8, NAN, NAN),  # nothing plottable: no panel
        make_row(phase="p2", aggressor="a2a", victim="ag", slowdown_med=5.0, slowdown_p99=6.0),
    )
    charts, notes = plot.build_charts(df, {"slowdown"})
    assert notes == []
    chart = by_file(charts)["slowdown_md_p2_perm_P1.png"]
    assert sorted(by_file(charts)) == ["slowdown_md_p2_ag_P1.png", "slowdown_md_p2_perm_P1.png"]
    assert [p.title for p in chart.panels] == [
        "a2a",
        "incast",
        "burst 0.01 s on, 0.0001 s off",
    ]
    a2a = chart.panels[0]
    assert [s.net for s in a2a.series] == ["C0", "C2"]
    c2 = a2a.series[1]
    assert c2.x == [8, 64] and c2.y[0] == 1.2 and math.isnan(c2.y[1])
    assert c2.y_p99 is not None and c2.y_p99[0] == 1.5
    assert chart.reference == 1.0 and chart.ylabel == "slowdown vs alone"
    assert chart.title == "Pairwise rotation against each aggressor, 1 path: slowdown vs alone"


def test_slowdown_smoke_prefix_and_empty_group_note() -> None:
    df = prepared(
        make_row(phase="p2", aggressor="a2a", smoke=True, slowdown_med=NAN, slowdown_p99=NAN)
    )
    charts, notes = plot.build_charts(df, {"slowdown"})
    assert charts == []
    assert len(notes) == 1 and "smoke_slowdown_md_p2_perm_P1.png" in notes[0]


def test_duplicates_keep_the_last_data_dir_and_are_counted() -> None:
    older = make_row(data_dir="2026-01-01", bw_gbps=1.0)
    newer = make_row(data_dir="2026-01-02", bw_gbps=5.0)
    other_size = make_row(data_dir="2026-01-01", msg_size=64, bw_gbps=2.0)
    df, dropped = plot.prepare(frame(newer, older, other_size))
    assert dropped == 1
    (chart,) = plot.build_charts(df, {"bw"})[0]
    assert chart.panels[0].series[0].x == [8, 64]
    assert chart.panels[0].series[0].y == [5.0, 2.0]


def test_read_summary_roundtrips_empty_cells(tmp_path: Path) -> None:
    path = tmp_path / "summary.csv"
    frame(make_row(), make_row(phase="p2", aggressor="a2a", slowdown_med=2.0)).to_csv(
        path, index=False
    )
    df = plot.read_summary(path)
    assert isinstance(df, pd.DataFrame)
    assert [isinstance(a, str) for a in df["aggressor"]] == [False, True]
    charts, _ = plot.build_charts(plot.prepare(df)[0], {"bw", "spread", "slowdown"})
    assert len(charts) == 3


def test_read_summary_reports_missing_columns(tmp_path: Path) -> None:
    path = tmp_path / "summary.csv"
    path.write_text("net,bw_gbps\nC0,1.0\n")
    result = plot.read_summary(path)
    assert isinstance(result, str) and "lacks columns" in result and "msg_size" in result


def test_main_without_matplotlib_exits_2_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "matplotlib", None)  # `import matplotlib` raises ImportError
    out = tmp_path / "plots"
    code = plot.main([str(tmp_path / "does-not-exist.csv"), "--out", str(out)])
    assert code == 2
    err = capsys.readouterr().err
    assert "plot.py needs matplotlib: pip install matplotlib" in err
    assert "into the Python you run this with" in err
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []
