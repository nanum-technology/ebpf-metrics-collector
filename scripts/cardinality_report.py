#!/usr/bin/env python3
"""카디널리티(시계열 수) 측정 — 수집기가 남긴 JSONL 로 measurement·태그별 시계열 수를 센다.

InfluxDB 의 cardinality() 는 시리즈가 수천만이면 끝나지 않는다 (2026-08 측정 실패).
이 스크립트는 DB 를 건드리지 않고 에이전트 노드의 로컬 JSONL 만 읽어 같은 값을 낸다.
태그 집합(measurement + 모든 태그) 하나가 InfluxDB 의 시계열 하나다.

    python3 scripts/cardinality_report.py                          # 오늘 하루 (config 의 base_path)
    python3 scripts/cardinality_report.py --day 2026-10-12
    python3 scripts/cardinality_report.py --files data/json/2026/10/13/network_*.jsonl
    python3 scripts/cardinality_report.py --drop-tags sport,pid     # 이 태그를 빼면 몇 개가 되는지 (what-if)
    python3 scripts/cardinality_report.py --ephemeral-from 32768    # 32768 이상 포트를 "eph" 로 묶으면 (what-if)
    python3 scripts/cardinality_report.py --json                    # 기계 판독용

보는 법
  series      : 그 measurement 의 고유 태그 집합 수 (= 새로 생긴 시계열 수의 상한)
  points      : 레코드 수
  top tags    : 고유값이 많은 태그 순 — 시계열을 불리는 주범
  what-if     : --drop-tags 의 태그를 지웠을 때 시계열 수 (특허 효과 측정·키 축소 판단 근거)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime


def iter_records(paths):
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    for m in rec.get("metrics", []) or []:
                        yield m
        except OSError as e:
            print(f"skip {p}: {e}", file=sys.stderr)


def day_files(base: str, day: date):
    # hourly: json/YYYY/MM/DD/*.jsonl · daily: json/YYYY/MM/*_<자정ms>.jsonl · per_cycle: json/*_<ms>.jsonl
    d = os.path.join(base, "json")
    hourly = glob.glob(os.path.join(d, day.strftime("%Y/%m/%d"), "*.jsonl"))
    if hourly:
        return sorted(hourly)
    start = int(datetime(day.year, day.month, day.day).timestamp() * 1000)
    end = start + 86400 * 1000
    out = []
    for p in glob.glob(os.path.join(d, "**", "*.jsonl"), recursive=True):
        try:
            ms = int(os.path.basename(p).rsplit("_", 1)[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        if start <= ms < end:
            out.append(p)
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=None, help="저장 루트 (기본: config/config.yaml 의 storage.base_path)")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (기본: 오늘)")
    ap.add_argument("--files", nargs="*", default=None, help="대상 JSONL 파일 직접 지정")
    ap.add_argument("--drop-tags", default="", help="쉼표 구분 — 지웠을 때의 시계열 수를 같이 계산")
    ap.add_argument("--ephemeral-from", type=int, default=0,
                    help="what-if: sport/dport 가 이 값 이상이면 'eph' 로 묶는다 (Linux 기본 임시 포트 32768~)")
    ap.add_argument("--top", type=int, default=5, help="measurement 별 상위 태그 수")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.files:
        paths = sorted(p for pat in a.files for p in glob.glob(pat))
        scope = f"{len(paths)} files"
    else:
        base = a.base
        if not base:
            try:
                import yaml
                with open("config/config.yaml", encoding="utf-8") as f:
                    base = yaml.safe_load(f)["storage"]["base_path"]
            except Exception:
                base = "./data"
        day = date.fromisoformat(a.day) if a.day else date.today()
        paths = day_files(base, day)
        scope = f"{day.isoformat()} ({len(paths)} files, {base})"
    if not paths:
        print("대상 파일 없음", file=sys.stderr)
        return 1

    drop = {t.strip() for t in a.drop_tags.split(",") if t.strip()}
    eph = a.ephemeral_from
    whatif = bool(drop or eph)

    def reduce(items):
        out = []
        for k, v in items:
            if k in drop:
                continue
            if eph and k in ("sport", "dport") and v.isdigit() and int(v) >= eph:
                v = "eph"
            out.append((k, v))
        return tuple(out)
    series = defaultdict(set)          # measurement -> {tagset}
    series_drop = defaultdict(set)     # measurement -> {tagset without drop tags}
    tag_values = defaultdict(lambda: defaultdict(set))   # measurement -> tag -> {values}
    points = defaultdict(int)

    for m in iter_records(paths):
        meas = m.get("measurement", "?")
        tags = m.get("tags", {}) or {}
        items = tuple(sorted((k, str(v)) for k, v in tags.items() if v is not None))
        series[meas].add(items)
        if whatif:
            series_drop[meas].add(reduce(items))
        for k, v in items:
            tag_values[meas][k].add(v)
        points[meas] += 1

    total = sum(len(s) for s in series.values())
    total_drop = sum(len(s) for s in series_drop.values()) if whatif else None
    report = {"scope": scope, "total_series": total, "drop_tags": sorted(drop), "ephemeral_from": eph or None,
              "total_series_after_drop": total_drop, "measurements": {}}
    for meas in sorted(series, key=lambda k: -len(series[k])):
        tv = sorted(((k, len(v)) for k, v in tag_values[meas].items()), key=lambda x: -x[1])
        report["measurements"][meas] = {
            "series": len(series[meas]),
            "points": points[meas],
            "series_after_drop": len(series_drop[meas]) if whatif else None,
            "top_tags": tv[: a.top],
        }

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    print(f"범위: {scope}")
    label = " + ".join(([", ".join(sorted(drop)) + " 제거"] if drop else []) + ([f"포트 {eph}+ → eph"] if eph else []))
    print(f"전체 시계열(고유 태그 집합): {total:,}" + (f"   →  {label} 시 {total_drop:,} ({total_drop/total*100:.1f}%)" if whatif else ""))
    print()
    hdr = f"{'measurement':<20} {'series':>10} {'points':>11}" + (f" {'what-if':>10}" if whatif else "") + "   상위 태그(고유값 수)"
    print(hdr)
    print("-" * len(hdr))
    for meas, r in report["measurements"].items():
        tags = ", ".join(f"{k}={n:,}" for k, n in r["top_tags"])
        line = f"{meas:<20} {r['series']:>10,} {r['points']:>11,}"
        if whatif:
            line += f" {r['series_after_drop']:>10,}"
        print(line + "   " + tags)
    return 0


if __name__ == "__main__":
    sys.exit(main())
