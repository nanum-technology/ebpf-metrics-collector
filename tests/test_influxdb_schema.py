# tests/test_influxdb_schema.py

import re
from pathlib import Path

import pytest
import yaml
from structlog.testing import capture_logs

from src.storage import influxdb_schema as schema
from src.storage.influxdb_schema import INFLUXDB_SCHEMA, COMMON_TAGS, SCHEMA_MODES, validate_points

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_warn_once():
    # validate_points() warns once per (measurement, kind, name) for the life of the process
    schema._warned.clear()
    yield
    schema._warned.clear()


def point(measurement="cpu_metrics", tags=None, fields=None):
    return {
        "measurement": measurement,
        "tags": {"node_id": "agent-001", "hostname": "node1", "ip": "192.0.2.1", "cpu_id": "0"} if tags is None else tags,
        "fields": {"usage_percent": 12.5, "context_switches": 10} if fields is None else fields,
        "timestamp": 1_700_000_000_000,
    }


def events(logs):
    return [e["event"] for e in logs]


# ── schema table ─────────────────────────────────────────────────────────────

def test_every_measurement_carries_common_tags():
    for name, sch in INFLUXDB_SCHEMA.items():
        assert set(COMMON_TAGS) <= set(sch["tags"]), name


def test_every_field_declares_a_known_type():
    for name, sch in INFLUXDB_SCHEMA.items():
        for field, desc in sch["fields"].items():
            assert schema._declared_type(desc) in schema._PY_TYPES, f"{name}.{field}: {desc!r}"


def test_collectors_only_emit_declared_measurements():
    # Static check — the collectors import bcc, so read their source instead of importing them
    emitted = set()
    for src in (ROOT / "src" / "collectors").glob("*_collector.py"):
        emitted |= set(re.findall(r'"measurement":\s*"([a-z_]+)"', src.read_text(encoding="utf-8")))
    assert emitted, "no measurement literals found — pattern out of date?"
    assert emitted <= set(INFLUXDB_SCHEMA), emitted - set(INFLUXDB_SCHEMA)


def test_shipped_config_uses_a_valid_schema_mode():
    cfg = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["influxdb"]["schema_mode"] in SCHEMA_MODES


# ── validate_points ──────────────────────────────────────────────────────────

def test_off_mode_is_a_no_op():
    pts = [point(tags={"bogus": "x"}, fields={"bogus": 1})]
    with capture_logs() as logs:
        assert validate_points(pts, mode="off") is pts
    assert logs == []


def test_conforming_points_pass_every_mode_silently():
    for mode in SCHEMA_MODES:
        pts = [point()]
        with capture_logs() as logs:
            assert validate_points(pts, mode=mode) == pts
        assert logs == [], mode


def test_warn_mode_reports_but_keeps_unknown_tags_and_fields():
    pts = [point(tags={**point()["tags"], "extra_tag": "x"}, fields={"usage_percent": 1.0, "extra_field": 2})]
    with capture_logs() as logs:
        out = validate_points(pts, mode="warn", collector="cpu")
    assert out is pts
    assert out[0]["tags"]["extra_tag"] == "x" and out[0]["fields"]["extra_field"] == 2
    assert {"schema_unknown_tag", "schema_unknown_field"} <= set(events(logs))


def test_strict_mode_strips_unknown_tags_and_fields_without_touching_input():
    original = point(tags={**point()["tags"], "extra_tag": "x"}, fields={"usage_percent": 1.0, "extra_field": 2})
    out = validate_points([original], mode="strict")
    assert len(out) == 1
    assert "extra_tag" not in out[0]["tags"] and "extra_field" not in out[0]["fields"]
    assert out[0]["fields"] == {"usage_percent": 1.0}
    assert "extra_tag" in original["tags"], "input point must not be mutated"


def test_strict_mode_drops_points_left_without_fields():
    pts = [point(fields={"only_unknown": 1}), point()]
    with capture_logs() as logs:
        out = validate_points(pts, mode="strict")
    assert len(out) == 1
    assert "schema_points_dropped" in events(logs)


def test_unknown_measurement_is_dropped_in_strict_and_kept_in_warn():
    pts = [point(measurement="not_a_measurement")]
    assert validate_points(pts, mode="strict") == []
    assert validate_points(pts, mode="warn") == pts


def test_type_mismatch_is_reported_but_value_is_never_converted():
    pts = [point(fields={"context_switches": "10"})]          # int64 field given a string
    with capture_logs() as logs:
        out = validate_points(pts, mode="strict")
    assert out[0]["fields"]["context_switches"] == "10"
    assert "schema_type_mismatch" in events(logs)


def test_bool_is_not_accepted_as_a_number():
    with capture_logs() as logs:
        validate_points([point(fields={"context_switches": True})], mode="warn")
    assert "schema_type_mismatch" in events(logs)


def test_int_is_accepted_for_float_fields():
    with capture_logs() as logs:
        validate_points([point(fields={"usage_percent": 5})], mode="warn")
    assert logs == []


def test_each_problem_is_reported_once_per_process():
    pts = [point(fields={"usage_percent": 1.0, "extra_field": 2})]
    with capture_logs() as logs:
        for _ in range(5):
            validate_points(pts, mode="warn")
    assert events(logs).count("schema_unknown_field") == 1


def test_unknown_mode_passes_points_through():
    pts = [point(fields={"bogus": 1})]
    with capture_logs() as logs:
        assert validate_points(pts, mode="lenient") is pts
    assert events(logs) == ["schema_mode_unknown"]
