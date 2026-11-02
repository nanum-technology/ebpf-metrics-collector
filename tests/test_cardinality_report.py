# tests/test_cardinality_report.py

import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "cardinality_report.py"


def load_module():
    spec = importlib.util.spec_from_file_location("cardinality_report", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def net(sport, dport="5000", pid=None):
    tags = {"node_id": "agent-001", "protocol": "TCP", "direction": "out",
            "src_ip": "192.0.2.1", "dst_ip": "192.0.2.2", "sport": str(sport), "dport": dport}
    if pid is not None:
        tags["pid"] = str(pid)
    return {"measurement": "network_metrics", "tags": tags, "fields": {"byte_count": 1}}


def write_cycles(path, cycles):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for metrics in cycles:
            f.write(json.dumps({"type": "network", "metrics": metrics}) + "\n")


def report(*args):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(SCRIPT), *args, "--json"], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_series_are_unique_tag_sets_and_points_are_records(tmp_path):
    f = tmp_path / "network_1.jsonl"
    # two cycles; the same three connections appear in both → 3 series, 6 points
    write_cycles(f, [[net(40001), net(40002), net(22)]] * 2)

    r = report("--files", str(f))

    assert r["total_series"] == 3
    m = r["measurements"]["network_metrics"]
    assert (m["series"], m["points"]) == (3, 6)
    assert dict(m["top_tags"])["sport"] == 3


def test_what_if_folds_ephemeral_ports_and_drops_tags(tmp_path):
    f = tmp_path / "network_1.jsonl"
    write_cycles(f, [[net(40001, pid=1), net(40002, pid=2), net(22, pid=3)]])

    r = report("--files", str(f), "--ephemeral-from", "32768")
    assert r["total_series_after_drop"] == 3           # pid still separates them

    r = report("--files", str(f), "--ephemeral-from", "32768", "--drop-tags", "pid")
    assert r["total_series_after_drop"] == 2           # sport=eph and sport=22
    assert r["drop_tags"] == ["pid"] and r["ephemeral_from"] == 32768


def test_no_input_files_is_an_error(tmp_path):
    p = subprocess.run([sys.executable, str(SCRIPT), "--files", str(tmp_path / "none_*.jsonl")],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert p.returncode == 1


def test_day_files_prefers_the_hourly_folder(tmp_path):
    mod = load_module()
    day = date(2026, 10, 20)
    hourly = tmp_path / "json" / "2026" / "10" / "20" / "cpu_1.jsonl"
    write_cycles(hourly, [[]])

    assert [Path(p) for p in mod.day_files(str(tmp_path), day)] == [hourly]


def test_day_files_selects_flat_files_by_timestamp(tmp_path):
    mod = load_module()
    day = date(2026, 10, 20)
    inside = int(datetime(2026, 10, 20, 12).timestamp() * 1000)
    before = int(datetime(2026, 10, 19, 23, 59).timestamp() * 1000)
    after = int(datetime(2026, 10, 21, 0, 0).timestamp() * 1000)
    for stamp in (inside, before, after):
        write_cycles(tmp_path / "json" / f"cpu_{stamp}.jsonl", [[]])

    got = [Path(p).name for p in mod.day_files(str(tmp_path), day)]
    assert got == [f"cpu_{inside}.jsonl"]
