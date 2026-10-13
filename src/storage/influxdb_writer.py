# src/storage/influxdb_writer.py

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Any
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from .influxdb_schema import validate_points
import structlog

logger = structlog.get_logger()


class InfluxDBWriter:
    def __init__(self, config: Dict[str, Any]):
        """
        InfluxDB Writer 초기화

        Args:
            config: InfluxDB 설정
                - url: InfluxDB URL
                - token: 인증 토큰
                - org: Organization 이름
                - bucket: Bucket 이름
        """
        self.config = config
        self.client = InfluxDBClient(
            url=config['url'],
            token=config['token'],
            org=config['org']
        )
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        self.bucket = config['bucket']

        # 쓰기 실패 재시도 (REQ-B-3) — 횟수를 제한한다. 무한 재시도·큐 적재는 ISSUE-036 의 재발이다.
        self.retry_attempts = int(config.get('retry_attempts', 3))
        self.retry_backoff_sec = float(config.get('retry_backoff_sec', 1.0))
        # 스키마 검증 (REQ-B-1) — off / warn / strict
        self.schema_mode = str(config.get('schema_mode', 'warn')).lower()

    async def write_metrics(self, measurement: str, tags: Dict[str, str],
                            fields: Dict[str, Any], timestamp: int = None):
        """
        메트릭 데이터를 InfluxDB에 기록

        Args:
            measurement: Measurement 이름 (테이블명)
            tags: 태그 딕셔너리
            fields: 필드 딕셔너리
            timestamp: Unix timestamp (nanoseconds)
        """
        try:
            point = Point(measurement)

            # 태그 추가
            for key, value in tags.items():
                if value is not None:
                    point = point.tag(key, str(value))

            # 필드 추가
            for key, value in fields.items():
                if value is not None:
                    if isinstance(value, bool):
                        point = point.field(key, value)
                    elif isinstance(value, int):
                        point = point.field(key, value)
                    elif isinstance(value, float):
                        point = point.field(key, value)
                    else:
                        point = point.field(key, str(value))

            # 타임스탬프 설정
            if timestamp:
                point = point.time(timestamp, WritePrecision.NS)
            else:
                point = point.time(datetime.now(timezone.utc), WritePrecision.NS)

            # 데이터 쓰기
            self.write_api.write(bucket=self.bucket, record=point)

        except Exception as e:
            logger.error("influxdb_write_error",
                         measurement=measurement,
                         error=str(e))
            raise

    async def write_batch(self, points: List[Dict[str, Any]], collector: str = ""):
        """
        배치로 여러 메트릭 기록

        Args:
            points: Point 리스트
                각 Point는 {measurement, tags, fields, timestamp} 포함
        """
        try:
            points = validate_points(points, mode=self.schema_mode, collector=collector)

            records = []
            for point_data in points:
                point = Point(point_data['measurement'])

                for key, value in point_data.get('tags', {}).items():
                    if value is not None:
                        point = point.tag(key, str(value))

                for key, value in point_data.get('fields', {}).items():
                    if value is not None:
                        point = point.field(key, value)

                timestamp = point_data.get('timestamp')
                if timestamp:
                    point = point.time(timestamp, WritePrecision.NS)

                records.append(point)

            if not records:
                return

            # 재시도 — 1·2·4초 지수 백오프. 마지막까지 실패하면 예외를 올려 호출부(main.py)가 그 배치를 버린다.
            attempt = 0
            while True:
                try:
                    self.write_api.write(bucket=self.bucket, record=records)
                    logger.info("influxdb_batch_written", collector=collector, count=len(records),
                                **({"retried": attempt} if attempt else {}))
                    return
                except Exception as e:
                    attempt += 1
                    if attempt >= self.retry_attempts:
                        raise
                    delay = self.retry_backoff_sec * (2 ** (attempt - 1))
                    logger.warning("influxdb_write_retry", collector=collector, attempt=attempt,
                                   max_attempts=self.retry_attempts, delay_sec=delay, error=str(e))
                    await asyncio.sleep(delay)

        except Exception as e:
            logger.error("influxdb_batch_write_error", collector=collector, error=str(e))
            raise

    def close(self):
        """연결 종료"""
        self.write_api.close()
        self.client.close()


# 사용 예시
if __name__ == "__main__":
    # <INFLUXDB_PASSWORD> / <INFLUXDB_PASSWORD>
    # 조직명 : nanum / 버켓명 : ebpf_metrics
    # 토큰명 : <INFLUXDB_TOKEN>
    config = {
        'url': 'http://192.0.2.226:8086',
        'token': '<INFLUXDB_TOKEN>',
        'org': 'nanum',
        'bucket': 'ebpf_metrics'
    }

    writer = InfluxDBWriter(config)

    # 단일 메트릭 쓰기
    asyncio.run(writer.write_metrics(
        measurement="network_metrics",
        tags={
            "node_id": "node-001",
            "hostname": "server-01",
            "interface": "eth0",
            "protocol": "TCP"
        },
        fields={
            "packet_count": 1000,
            "byte_count": 1500000,
            "rtt_ms": 12.5
        }
    ))