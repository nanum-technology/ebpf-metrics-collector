# src/storage/tiered_storage.py

import os
import json
import asyncio
import aiofiles
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard as zstd
import structlog

logger = structlog.get_logger()


class TieredStorage:
    """계층화된 파일 저장 시스템"""

    def __init__(self, base_path: str = "./data", json_split: str = "per_cycle",
                 node_info: Dict[str, str] = None):
        """
        Args:
            base_path: 저장 루트 경로
            node_info: 노드 정보 (node_id, hostname, ip). JSONL 의 collector_info 에 기록된다.
                       미전달 시 collector_info 가 빈 값으로 나가므로 반드시 넘길 것.
            json_split: JSONL 파일 분할 방식
                - "per_cycle": (기존/기본) 수집 주기마다 새 파일 1개. KETI 기존 연동 호환.
                - "hourly"   : 날짜 폴더 + 시간(1h) 단위로 한 파일에 누적. 파일 수 급감.
                - "daily"    : 날짜 폴더 + 일(1d) 단위로 한 파일에 누적. 파일 수 최소.
        """
        self.base_path = Path(base_path)
        self.json_path = self.base_path / "json"
        self.parquet_path = self.base_path / "parquet"
        self.archive_path = self.base_path / "archive"
        self.json_split = json_split
        self.node_info = node_info or {}

        # 디렉토리 생성
        for path in [self.json_path, self.parquet_path, self.archive_path]:
            path.mkdir(parents=True, exist_ok=True)

    async def write_json(self, metric_type: str, data: Dict[str, Any]):
        """
        JSON 형식으로 저장 (최근 7일)

        json_split 설정에 따라 파일 위치/이름이 달라진다.
          - "per_cycle"(기본): data/json/{metric_type}_{epoch ms}.jsonl
                               수집 주기마다 새 파일. (KETI 기존 연동 호환)
          - "hourly"        : data/json/{YYYY}/{MM}/{DD}/{metric_type}_{정시 epoch ms}.jsonl
                               해당 시간(1h)의 모든 주기를 한 파일에 한 줄씩 누적.
          - "daily"         : data/json/{YYYY}/{MM}/{metric_type}_{자정 epoch ms}.jsonl
                               해당 일(1d)의 모든 주기를 한 파일에 한 줄씩 누적.
        어느 방식이든 레코드(줄) 내용/스키마는 동일하다.
        """
        try:
            now = datetime.now()
            timestamp = int(now.timestamp() * 1000)  # 줄(레코드) 단위 타임스탬프

            if self.json_split in ("hourly", "daily"):
                # 파일명을 시간/일 버킷으로 고정 → 같은 버킷은 같은 파일에 append
                # 폴더는 버킷보다 한 단계 위(hourly=연/월/일, daily=연/월)로 중첩
                if self.json_split == "hourly":
                    bucket_dt = now.replace(minute=0, second=0, microsecond=0)
                    subdir = now.strftime("%Y/%m/%d")  # 연/월/일
                else:  # daily
                    bucket_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    subdir = now.strftime("%Y/%m")     # 연/월
                bucket_ms = int(bucket_dt.timestamp() * 1000)
                out_dir = self.json_path / subdir
                out_dir.mkdir(parents=True, exist_ok=True)
                filename = f"{metric_type}_{bucket_ms}.jsonl"
                filepath = out_dir / filename
            else:
                # per_cycle (기존 동작): 주기마다 새 파일
                filename = f"{metric_type}_{timestamp}.jsonl"
                filepath = self.json_path / filename

            # JSONL 형식으로 저장 (한 줄에 하나의 JSON)
            async with aiofiles.open(filepath, 'a') as f:
                # 노드 정보 추가
                # data(수집기 반환값)에는 node 정보가 최상위에 없다 — 각 metric 의 tags 안에 들어간다.
                # 따라서 생성 시 주입받은 self.node_info 를 기본 출처로 쓰고,
                # data 최상위에 값이 들어온 경우에만 그쪽을 우선한다.
                data_with_node = {
                    "collector_info": {
                        "hostname": data.get("hostname") or self.node_info.get("hostname", ""),
                        "node_id": data.get("node_id") or self.node_info.get("node_id", ""),
                        "ip": data.get("ip") or self.node_info.get("ip", "")
                    },
                    "timestamp": str(timestamp),
                    "date": datetime.now().strftime("%Y%m%dZ%H%M%S%f")[:-3],
                    **data
                }
                await f.write(json.dumps(data_with_node, ensure_ascii=False) + '\n')

            logger.debug("json_written", filename=filename)

        except Exception as e:
            logger.error("json_write_error", error=str(e))
            raise

    async def convert_to_parquet(self, date: datetime):
        """
        7일 경과한 JSON 파일을 Parquet으로 변환

        보존 규칙 (2026-10-13 — 보존율 3.9% 결함 수정)
          - 변환 단위는 (metric_type, 날짜) 이고, **그 날짜의 마지막 버킷까지 모두 7일을 넘긴 뒤에만** 변환한다.
            이전에는 시간(hour) 파일이 7일 경계를 하나씩 넘을 때마다 그 날짜 parquet 을 새로 써서
            (pq.write_table 덮어쓰기) 마지막 한 시간치만 남았다 (실측 보존율 3.9%).
          - 같은 날짜의 parquet 이 이미 있으면 (재시작·과거 결함 잔재) 읽어서 합친 뒤 다시 쓴다. 덮어쓰지 않는다.
          - 쓰기는 임시 파일에 한 뒤 교체(os.replace) — 도중에 죽어도 기존 parquet 이 깨지지 않는다.
        """
        try:
            cutoff_date = date - timedelta(days=7)

            # 7일 이전 파일을 (metric_type, 날짜) 단위로 묶음
            # 하위 날짜 폴더까지 재귀 탐색(rglob). 같은 날짜의 시간(hour)별 파일은
            # 하나의 일(day) parquet으로 합친다.
            groups: Dict[tuple, List[Path]] = {}
            for json_file in self.json_path.rglob("*.jsonl"):
                try:
                    parts = json_file.stem.split('_')
                    file_dt = datetime.fromtimestamp(int(parts[1]) / 1000)
                except (IndexError, ValueError):
                    continue
                # 날짜가 끝나는 시각(다음날 0시)이 cutoff 를 지나야 그 날짜가 "완결"된 것이다.
                # per_cycle · hourly · daily 어느 분할 방식이든 같은 기준이 적용된다.
                day_start = file_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_start + timedelta(days=1)
                if day_end < cutoff_date:
                    key = (parts[0], day_start.strftime('%Y%m%d'))
                    groups.setdefault(key, []).append(json_file)

            for (metric_type, day_str), files in groups.items():
                records = []
                for json_file in files:
                    async with aiofiles.open(json_file, 'r') as f:
                        async for line in f:
                            line = line.strip()
                            if line:
                                records.append(json.loads(line))

                if not records:
                    for jf in files:
                        jf.unlink()
                    continue

                df = pd.DataFrame(records)
                parquet_file = self.parquet_path / f"{metric_type}_{day_str}.parquet"

                # 기존 parquet 과 병합 — 덮어쓰면 앞서 변환된 행이 사라진다
                existing_rows = 0
                if parquet_file.exists():
                    try:
                        existing_df = pq.read_table(parquet_file).to_pandas()
                        existing_rows = len(existing_df)
                        df = pd.concat([existing_df, df], ignore_index=True, sort=False)
                    except Exception as e:
                        # 읽을 수 없는 parquet 은 덮어쓰지 않고 옆으로 치운다
                        broken = parquet_file.with_suffix(f".broken-{int(date.timestamp())}.parquet")
                        parquet_file.rename(broken)
                        logger.warning("parquet_existing_unreadable",
                                       parquet_file=parquet_file.name, moved_to=broken.name, error=str(e))

                table = pa.Table.from_pandas(df)
                tmp_file = parquet_file.with_suffix(".parquet.tmp")
                pq.write_table(table, tmp_file, compression='snappy')
                os.replace(tmp_file, parquet_file)

                # 원본 JSON 삭제
                for jf in files:
                    jf.unlink()

                logger.info("parquet_converted",
                            metric_type=metric_type,
                            day=day_str,
                            json_files=len(files),
                            rows_added=len(records),
                            rows_existing=existing_rows,
                            rows_total=len(df),
                            parquet_file=parquet_file.name)

            # 비어버린 날짜 폴더 정리 (연/월/일 중첩까지 하위부터 제거)
            for root, dirs, files in os.walk(self.json_path, topdown=False):
                p = Path(root)
                if p != self.json_path and not any(p.iterdir()):
                    p.rmdir()

        except Exception as e:
            logger.error("parquet_conversion_error", error=str(e))
            raise

    async def compress_archive(self, date: datetime):
        """
        30일 경과한 Parquet 파일을 압축
        """
        try:
            cutoff_date = date - timedelta(days=30)
            target_files = []

            # 30일 이전 파일 찾기
            for parquet_file in self.parquet_path.glob("*.parquet"):
                file_date_str = parquet_file.stem.split('_')[-1]
                file_date = datetime.strptime(file_date_str, '%Y%m%d')
                if file_date < cutoff_date:
                    target_files.append(parquet_file)

            for parquet_file in target_files:
                # Zstandard 압축
                archive_file = self.archive_path / f"{parquet_file.stem}.parquet.zst"

                async with aiofiles.open(parquet_file, 'rb') as f_in:
                    data = await f_in.read()

                cctx = zstd.ZstdCompressor(level=9)
                compressed_data = cctx.compress(data)

                async with aiofiles.open(archive_file, 'wb') as f_out:
                    await f_out.write(compressed_data)

                # 원본 Parquet 삭제
                parquet_file.unlink()

                logger.info("archive_compressed",
                            parquet_file=parquet_file.name,
                            archive_file=archive_file.name,
                            compression_ratio=len(data) / len(compressed_data))

        except Exception as e:
            logger.error("archive_compression_error", error=str(e))
            raise

    async def cleanup_old_files(self):
        """오래된 파일 정리 작업"""
        now = datetime.now()

        # JSON → Parquet (7일)
        await self.convert_to_parquet(now)

        # Parquet → Archive (30일)
        await self.compress_archive(now)

        logger.info("cleanup_completed", timestamp=now.isoformat())


# 백그라운드 정리 작업
async def cleanup_scheduler(storage: TieredStorage, interval_hours: int = 1):
    """주기적으로 파일 정리"""
    while True:
        await storage.cleanup_old_files()
        await asyncio.sleep(interval_hours * 3600)