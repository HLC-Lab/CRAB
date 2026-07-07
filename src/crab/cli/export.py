import csv
import json
import sys
from pathlib import Path

_SYSTEM_CSVS = frozenset({"metadata.csv", "description.csv"})
_DASHBOARD_TEMPLATE = Path(__file__).resolve().parents[1] / "crab_dashboard.html"


def _clean_csv_name(stem: str) -> str:
    """Convert a CRAB CSV stem to a human-readable experiment label.

    'data_app_0' → 'App 0', 'data_app_1' → 'App 1', others → title-cased.
    """
    if stem.startswith("data_app_") and stem[9:].isdigit():
        return f"App {stem[9:]}"
    return stem.replace("_", " ").replace("-", " ").title()


def _load_config_json(data_dir: Path) -> dict | None:
    """Load a job's config.json, tolerant of it being absent or malformed."""
    config_path = data_dir / "config.json"
    if not config_path.is_file():
        return None
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_app_label(config: dict | None, experiment_name: str, csv_stem: str) -> str:
    """Resolve a real app name from config.json for a 'data_app_N' stem.

    Falls back to _clean_csv_name for a non-app stem or any lookup failure
    (missing config, missing experiment/app key).
    """
    if config is not None and csv_stem.startswith("data_app_") and csv_stem[9:].isdigit():
        app_id = csv_stem[9:]
        try:
            path = config["experiments"][experiment_name]["apps"][app_id]["path"]
            return Path(path).stem
        except (KeyError, TypeError):
            pass
    return _clean_csv_name(csv_stem)


def parse_csv(path: Path) -> list[dict]:
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


def _load_dir_csvs(directory: Path, lab_name: str, labs: dict, config: dict | None) -> None:
    """Load every non-system CSV in directory as a separate experiment under lab_name."""
    data_csvs = sorted(
        f
        for f in directory.iterdir()
        if f.is_file() and f.suffix == ".csv" and f.name not in _SYSTEM_CSVS
    )
    for csv_path in data_csvs:
        rows = parse_csv(csv_path)
        if rows:
            label = resolve_app_label(config, lab_name, csv_path.stem)
            labs.setdefault(lab_name, {})[label] = rows


def collect_result_data(data_dir: Path) -> dict[str, dict[str, list[dict]]]:
    """Walk data_dir and return {lab: {experiment: [rows]}}.

    Directory → lab/experiment mapping:
      root/file.csv          → Root Lab / file
      root/exp_dir/          → exp_dir / App N   (dir contains data CSVs directly)
      root/lab_dir/exp_dir/  → exp_dir / App N   (nested: lab_dir's subdirs have CSVs)
    """
    labs: dict[str, dict[str, list[dict]]] = {}

    entries = sorted(data_dir.iterdir())
    csvs_at_root = [
        e for e in entries if e.is_file() and e.suffix == ".csv" and e.name not in _SYSTEM_CSVS
    ]
    dirs_at_root = [e for e in entries if e.is_dir()]

    if csvs_at_root:
        root_config = _load_config_json(data_dir)
        for csv_path in csvs_at_root:
            rows = parse_csv(csv_path)
            if rows:
                label = resolve_app_label(root_config, "Root Lab", csv_path.stem)
                labs.setdefault("Root Lab", {})[label] = rows

    for d in dirs_at_root:
        sub_entries = sorted(d.iterdir())
        sub_csvs = [
            e
            for e in sub_entries
            if e.is_file() and e.suffix == ".csv" and e.name not in _SYSTEM_CSVS
        ]
        sub_dirs = [e for e in sub_entries if e.is_dir()]

        if sub_csvs or not sub_dirs:
            # d is an experiment directory (has data CSVs directly); config.json,
            # written by Engine.run() as a sibling of the experiment dir, is in data_dir.
            _load_dir_csvs(d, d.name, labs, _load_config_json(data_dir))
        else:
            # d is a job/lab directory; its subdirs are experiment directories,
            # and config.json is a sibling of each of them, i.e. inside d.
            lab_config = _load_config_json(d)
            for exp_dir in sub_dirs:
                exp_csvs = [
                    e
                    for e in sorted(exp_dir.iterdir())
                    if e.is_file() and e.suffix == ".csv" and e.name not in _SYSTEM_CSVS
                ]
                if exp_csvs:
                    _load_dir_csvs(exp_dir, exp_dir.name, labs, lab_config)

    return labs


def export_dashboard(data_dir: Path, output: Path) -> None:
    if not _DASHBOARD_TEMPLATE.is_file():
        print(f"[ERROR] Dashboard template not found: {_DASHBOARD_TEMPLATE}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Scanning {data_dir} …")
    labs = collect_result_data(data_dir)
    total_exps = sum(len(exps) for exps in labs.values())

    if total_exps == 0:
        print(f"[ERROR] No CSV data found in {data_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Found {total_exps} experiment(s) across {len(labs)} lab(s)")
    for lab, exps in labs.items():
        for exp in exps:
            print(f"    {lab} / {exp}")

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
