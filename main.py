# main.py (업데이트)

import asyncio
import signal
import sys
import argparse
from pathlib import Path
import yaml
from src.collectors.network_collector import NetworkCollector
from src.collectors.cpu_collector import CPUCollector
from src.collectors.memory_collector import MemoryCollector
from src.collectors.filesystem_collector import FilesystemCollector
from src.collectors.process_collector import ProcessCollector
from src.collectors.syscall_collector import SyscallCollector
from src.collectors.container_collector import ContainerCollector
from src.collectors.gpu_collector import GPUCollector
from src.storage.influxdb_writer import InfluxDBWriter
from src.storage.file_writer import FileWriter
from src.storage.tiered_storage import cleanup_scheduler
from src.utils.node_info import get_node_info
from src.utils.logger import setup_logging
import structlog

logger = None


class MetricCollectorManager:
    """메트릭 수집 관리자"""

    def __init__(self, config_path: str, node_id: str = None):
        global logger

        # 설정 로드
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        # 로깅 설정
        logger = setup_logging(
            log_level=self.config.get('logging', {}).get('level', 'INFO'),
            log_file=self.config.get('logging', {}).get('file')
        )

        # 노드 정보
        self.node_info = get_node_info(node_id or self.config.get('node_id'))
        logger.info("node_initialized", node_info=self.node_info)

        # 저장소 초기화
        self.influxdb_writer = InfluxDBWriter(self.config['influxdb'])
        self.file_writer = FileWriter(
            self.config['storage']['base_path'],
            json_split=self.config['storage'].get('json_split', 'per_cycle'),
            node_info=self.node_info
        )

        # 수집기 초기화
        self.collectors = []
        self._init_collectors()

        # 종료 플래그
        self.is_running = False

    def _init_collectors(self):
        """수집기 초기화"""
        collector_config = self.config.get('collectors', {})

        collector_classes = {
            'network': NetworkCollector,
            'syscall': SyscallCollector,
            'cpu': CPUCollector,
            'memory': MemoryCollector,
            'filesystem': FilesystemCollector,
            'process': ProcessCollector,
            'container': ContainerCollector,
            'gpu': GPUCollector
        }

        for name, collector_class in collector_classes.items():
            if collector_config.get(name, {}).get('enabled', False):
                try:
                    collector = collector_class(
                        collector_config[name],
                        self.node_info
                    )
                    self.collectors.append(collector)
                    logger.info("collector_initialized", collector=name)
                except Exception as e:
                    logger.error("collector_init_failed",
                                 collector=name,
                                 error=str(e))

        logger.info("all_collectors_initialized", count=len(self.collectors))

    async def collect_loop(self):
        """메트릭 수집 루프"""
        interval = self.config.get('collection_interval', 10)

        while self.is_running:
            try:
                # 모든 수집기에서 메트릭 수집
                tasks = [collector.collect() for collector in self.collectors]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # 수집된 메트릭 처리
                for result in results:
                    if isinstance(result, Exception):
                        logger.error("collection_error", error=str(result))
                        continue

                    if not result or not result.get('metrics'):
                        continue

                    # InfluxDB 저장
                    try:
                        await self.influxdb_writer.write_batch(result['metrics'], collector=result.get('type', ''))
                    except Exception as e:
                        logger.error("influxdb_write_failed", error=str(e))

                    # 파일 저장
                    try:
                        await self.file_writer.write_metrics(result)
                    except Exception as e:
                        logger.error("file_write_failed", error=str(e))

                await asyncio.sleep(interval)

            except Exception as e:
                logger.error("collect_loop_error", error=str(e))
                await asyncio.sleep(interval)

    async def start(self):
        """수집 시작"""
        logger.info("metric_collector_starting")

        # 수집기 시작
        for collector in self.collectors:
            try:
                collector.start()
            except Exception as e:
                logger.error("collector_start_failed",
                             collector=collector.__class__.__name__,
                             error=str(e))

        self.is_running = True

        # 백그라운드 작업 시작
        tasks = [
            self.collect_loop(),
            cleanup_scheduler(
                self.file_writer.storage,
                interval_hours=self.config.get('cleanup_interval_hours', 1)
            )
        ]

        await asyncio.gather(*tasks)

    async def stop(self):
        """수집 중지"""
        logger.info("metric_collector_stopping")
        self.is_running = False

        # 수집기 중지
        for collector in self.collectors:
            try:
                collector.stop()
            except Exception as e:
                logger.error("collector_stop_failed",
                             collector=collector.__class__.__name__,
                             error=str(e))

        # 연결 종료
        try:
            self.influxdb_writer.close()
        except Exception as e:
            logger.error("influxdb_close_failed", error=str(e))

        logger.info("metric_collector_stopped")


async def main():
    """메인 함수"""
    parser = argparse.ArgumentParser(description='eBPF Metric Collector')
    parser.add_argument('--config', default='./config/config.yaml',
                        help='Configuration file path')
    parser.add_argument('--node-id', help='Node ID override')
    args = parser.parse_args()

    manager = MetricCollectorManager(args.config, args.node_id)

    # 시그널 핸들러
    loop = asyncio.get_event_loop()

    def signal_handler(sig):
        logger.info("shutdown_signal_received", signal=sig)
        asyncio.create_task(manager.stop())
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    try:
        await manager.start()
    except KeyboardInterrupt:
        await manager.stop()
    except Exception as e:
        logger.error("main_error", error=str(e))
        await manager.stop()
        sys.exit(1)


if __name__ == "__main__":
    # Root 권한 체크
    import os

    if os.geteuid() != 0:
        print("Error: This program requires root privileges to load eBPF programs.")
        print("Please run with sudo: sudo python main.py")
        sys.exit(1)

    asyncio.run(main())