# src/storage/file_writer.py

import asyncio
from typing import Dict, Any
from .tiered_storage import TieredStorage
import structlog

logger = structlog.get_logger()


class FileWriter:
    """파일 기반 저장소 Writer"""

    def __init__(self, base_path: str = "./data", json_split: str = "per_cycle",
                 node_info: Dict[str, str] = None):
        self.storage = TieredStorage(base_path, json_split=json_split,
                                     node_info=node_info)

    async def write_metrics(self, metrics: Dict[str, Any]):
        """메트릭을 파일로 저장"""
        try:
            metric_type = metrics.get('type', 'unknown')

            # JSON 저장
            await self.storage.write_json(metric_type, metrics)

        except Exception as e:
            logger.error("file_write_error", error=str(e))
            raise

    async def write_batch(self, batch_metrics: list):
        """배치로 저장"""
        tasks = [self.write_metrics(m) for m in batch_metrics]
        await asyncio.gather(*tasks)