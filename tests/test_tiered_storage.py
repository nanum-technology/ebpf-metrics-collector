# tests/test_tiered_storage.py

import asyncio
import json
from datetime import datetime, timedelta

import pyarrow.parquet as pq
import pytest
import zstandard as zstd

from src.storage import tiered_storage
from src.storage.tiered_storage import TieredStorage

NODE = {"node_id": "agent-001", "hostname": "node1", "ip": "192.0.2.1"}
NOW = datetime(2026, 10, 20, 14, 30, 15)


def run(coro):
    return asyncio.run(coro)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def record(i: int, metric_type: str = "cpu") -> dict:
    return {
        "collector_info": NODE,
        "timestamp": str(1_700_000_000_000 + i),
        "type": metric_type,
        "metrics": [{"measurement": f"{metric_type}_metrics", "tags": {"cpu_id": str(i % 4)}, "fields": {"value": i}}],
    }


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def frozen_now(monkeypatch):
    """Pin datetime.now() inside tiered_storage so bucket boundaries are deterministic."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(tiered_storage, "datetime", Frozen)
    return NOW


# ── write_json layouts ───────────────────────────────────────────────────────

def test_per_cycle_writes_one_file_per_cycle_with_node_info(tmp_path, frozen_now):
    st = TieredStorage(str(tmp_path), json_split="per_cycle", node_info=NODE)
    run(st.write_json("cpu", {"type": "cpu", "metrics": []}))

    files = list((tmp_path / "json").glob("*.jsonl"))
    assert [f.name for f in files] == [f"cpu_{ms(NOW)}.jsonl"]
    rec = json.loads(files[0].read_text(encoding="utf-8"))
    assert rec["collector_info"] == NODE
    assert rec["type"] == "cpu" and rec["metrics"] == []


def test_hourly_appends_every_cycle_of_the_hour_to_one_file(tmp_path, frozen_now):
    st = TieredStorage(str(tmp_path), json_split="hourly", node_info=NODE)
    for _ in range(3):
        run(st.write_json("network", {"type": "network", "metrics": []}))

    hour = NOW.replace(minute=0, second=0, microsecond=0)
    path = tmp_path / "json" / "2026" / "10" / "20" / f"network_{ms(hour)}.jsonl"
    assert path.exists()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_daily_appends_every_cycle_of_the_day_to_one_file(tmp_path, frozen_now):
    st = TieredStorage(str(tmp_path), json_split="daily", node_info=NODE)
    for _ in range(2):
        run(st.write_json("memory", {"type": "memory", "metrics": []}))

    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    path = tmp_path / "json" / "2026" / "10" / f"memory_{ms(midnight)}.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_top_level_node_fields_override_constructor_node_info(tmp_path, frozen_now):
    st = TieredStorage(str(tmp_path), node_info=NODE)
    run(st.write_json("cpu", {"type": "cpu", "node_id": "agent-009", "metrics": []}))
    rec = json.loads(next((tmp_path / "json").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert rec["collector_info"]["node_id"] == "agent-009"
    assert rec["collector_info"]["hostname"] == NODE["hostname"]


# ── JSONL → Parquet (7 days) ─────────────────────────────────────────────────

def hourly_day(tmp_path, day: datetime, metric_type="cpu", per_file=3):
    """24 hourly files for one day, `per_file` records each — the layout json_split=hourly produces."""
    n = 0
    for h in range(24):
        bucket = day.replace(hour=h, minute=0, second=0, microsecond=0)
        path = tmp_path / "json" / bucket.strftime("%Y/%m/%d") / f"{metric_type}_{ms(bucket)}.jsonl"
        write_jsonl(path, [record(n + i, metric_type) for i in range(per_file)])
        n += per_file
    return n


def test_completed_day_is_converted_with_every_row_kept(tmp_path):
    # Regression: each hour file used to overwrite the day's parquet, keeping ~1/24 of the rows.
    st = TieredStorage(str(tmp_path))
    day = NOW - timedelta(days=10)
    rows = hourly_day(tmp_path, day)

    run(st.convert_to_parquet(NOW))

    pq_file = tmp_path / "parquet" / f"cpu_{day.strftime('%Y%m%d')}.parquet"
    assert pq.read_table(pq_file).num_rows == rows == 72
    assert list((tmp_path / "json").rglob("*.jsonl")) == []
    assert list((tmp_path / "json").iterdir()) == [], "empty date folders should be removed"


def test_day_still_inside_the_window_is_left_alone(tmp_path):
    st = TieredStorage(str(tmp_path))
    hourly_day(tmp_path, NOW - timedelta(days=7))      # ends after the 7-day cutoff

    run(st.convert_to_parquet(NOW))

    assert len(list((tmp_path / "json").rglob("*.jsonl"))) == 24
    assert list((tmp_path / "parquet").glob("*.parquet")) == []


def test_late_files_are_merged_into_an_existing_day_parquet(tmp_path):
    st = TieredStorage(str(tmp_path))
    day = NOW - timedelta(days=10)
    first = hourly_day(tmp_path, day, per_file=2)
    run(st.convert_to_parquet(NOW))

    late = day.replace(hour=23)                       # e.g. a file restored after a restart
    write_jsonl(tmp_path / "json" / f"cpu_{ms(late)}.jsonl", [record(1000 + i) for i in range(5)])
    run(st.convert_to_parquet(NOW))

    table = pq.read_table(tmp_path / "parquet" / f"cpu_{day.strftime('%Y%m%d')}.parquet")
    assert table.num_rows == first + 5
    assert list((tmp_path / "parquet").glob("*.tmp")) == []


def test_metric_types_and_days_get_separate_parquet_files(tmp_path):
    st = TieredStorage(str(tmp_path))
    d1, d2 = NOW - timedelta(days=12), NOW - timedelta(days=11)
    hourly_day(tmp_path, d1, "cpu", per_file=1)
    hourly_day(tmp_path, d2, "cpu", per_file=1)
    hourly_day(tmp_path, d1, "network", per_file=1)

    run(st.convert_to_parquet(NOW))

    names = sorted(p.name for p in (tmp_path / "parquet").glob("*.parquet"))
    assert names == sorted([f"cpu_{d1:%Y%m%d}.parquet", f"cpu_{d2:%Y%m%d}.parquet", f"network_{d1:%Y%m%d}.parquet"])


def test_files_without_a_timestamp_in_the_name_are_ignored(tmp_path):
    st = TieredStorage(str(tmp_path))
    stray = tmp_path / "json" / "notes.jsonl"
    write_jsonl(stray, [record(0)])

    run(st.convert_to_parquet(NOW))

    assert stray.exists()


# ── Parquet → Zstandard archive (30 days) ────────────────────────────────────

def test_old_parquet_is_archived_losslessly_and_recent_parquet_stays(tmp_path):
    st = TieredStorage(str(tmp_path))
    old_day, recent_day = NOW - timedelta(days=40), NOW - timedelta(days=10)
    hourly_day(tmp_path, old_day, per_file=1)
    hourly_day(tmp_path, recent_day, per_file=1)
    run(st.convert_to_parquet(NOW))
    old_pq = tmp_path / "parquet" / f"cpu_{old_day:%Y%m%d}.parquet"
    original_bytes = old_pq.read_bytes()

    run(st.compress_archive(NOW))

    archive = tmp_path / "archive" / f"cpu_{old_day:%Y%m%d}.parquet.zst"
    assert not old_pq.exists()
    assert zstd.ZstdDecompressor().decompress(archive.read_bytes()) == original_bytes
    assert (tmp_path / "parquet" / f"cpu_{recent_day:%Y%m%d}.parquet").exists()
