import csv
import json
import sys
from pathlib import Path


_CSV_NAMES = ["data_app_0.csv", "data_app_1.csv", "data.csv", "results.csv", "output.csv"]
_DASHBOARD_TEMPLATE = Path(__file__).resolve().parents[3] / "crab_dashboard.html"


def _find_csv(directory: Path) -> Path | None:
    for name in _CSV_NAMES:
        p = directory / name
        if p.is_file():
            return p
    csvs = sorted(directory.glob("*.csv"))
    return csvs[0] if csvs else None


def _parse_csv(path: Path) -> list[dict]:
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            parsed = {}
            for k, v in row.items():
                k = (k or "").strip()
                v = (v or "").strip()
                if v:
                    try:
                        num = float(v)
                        try:
                            i = int(num)
                            parsed[k] = i if i == num else num
                        except (OverflowError, ValueError):
                            parsed[k] = num
                    except ValueError:
                        parsed[k] = v
                else:
                    parsed[k] = v
            rows.append(parsed)
    return rows


def _collect_data(data_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """Walk data_dir and return {lab: {experiment: [rows]}}.

    Mirrors the directory-traversal logic in the dashboard's ServerLoader.fetch():
      root/file.csv          → Root Lab / file
      root/exp_dir/          → Root Lab / exp_dir  (contains CSVs or has no subdirs)
      root/lab_dir/exp_dir/  → lab_dir / exp_dir   (subdirs that themselves contain CSVs)
    """
    labs: dict[str, dict[str, list[dict]]] = {}

    def add(lab: str, exp: str, rows: list[dict]) -> None:
        if rows:
            labs.setdefault(lab, {})[exp] = rows

    entries = sorted(data_dir.iterdir())
    csvs_at_root = [e for e in entries if e.is_file() and e.suffix == ".csv"]
    dirs_at_root = [e for e in entries if e.is_dir()]

    for csv_path in csvs_at_root:
        add("Root Lab", csv_path.stem, _parse_csv(csv_path))

    for d in dirs_at_root:
        sub_entries = sorted(d.iterdir())
        sub_csvs = [e for e in sub_entries if e.is_file() and e.suffix == ".csv"]
        sub_dirs = [e for e in sub_entries if e.is_dir()]

        if sub_csvs or not sub_dirs:
            # d is an experiment directory (flat structure)
            csv_path = _find_csv(d)
            if csv_path:
                add("Root Lab", d.name, _parse_csv(csv_path))
        else:
            # d is a lab directory, its subdirs are experiments
            for exp_dir in sub_dirs:
                csv_path = _find_csv(exp_dir)
                if csv_path:
                    add(d.name, exp_dir.name, _parse_csv(csv_path))

    return labs


def export_dashboard(data_dir: Path, output: Path) -> None:
    if not _DASHBOARD_TEMPLATE.is_file():
        print(f"[ERROR] Dashboard template not found: {_DASHBOARD_TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Scanning {data_dir} …")
    labs = _collect_data(data_dir)
    total_exps = sum(len(exps) for exps in labs.values())

    if total_exps == 0:
        print(f"[ERROR] No CSV data found in {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Found {total_exps} experiment(s) across {len(labs)} lab(s)")

    html = _DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    data_json = json.dumps({"labs": labs}, separators=(",", ":"))

    inject = (
        "<style>.tbox{display:none!important}</style>\n"
        f"<script>const CRAB_EMBEDDED_DATA={data_json};</script>\n"
    )
    html = html.replace("</head>", inject + "</head>", 1)

    output.write_text(html, encoding="utf-8")
    print(f"[+] Exported to {output}")


def handle_export(args):
    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        print(f"[ERROR] Not a directory: {data_dir}", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output) if args.output else Path("crab_export.html")
    export_dashboard(data_dir, output)
