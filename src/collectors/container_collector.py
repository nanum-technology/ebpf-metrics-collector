# src/collectors/container_collector.py

import time
import os
from pathlib import Path
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class ContainerCollector(BaseCollector):
    """컨테이너/Kubernetes 메트릭 수집기"""

    def __init__(self, config: Dict[str, Any], node_info: Dict[str, str]):
        super().__init__(config, node_info)
        self.cgroup_base = Path("/sys/fs/cgroup")

    def get_ebpf_program(self) -> str:
        return """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

// 컨테이너 메트릭 구조체
struct container_stats_t {
    u64 cpu_usage_ns;
    u64 memory_usage;
    u64 network_rx_bytes;
    u64 network_tx_bytes;
};

BPF_HASH(container_stats, u32, struct container_stats_t);

// cgroup 이벤트 추적
TRACEPOINT_PROBE(cgroup, cgroup_attach_task) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;

    struct container_stats_t *stats, zero = {};
    stats = container_stats.lookup_or_init(&pid, &zero);

    return 0;
}
"""

    def _read_cgroup_value(self, cgroup_path: Path, filename: str) -> int:
        """cgroup 단일 값 읽기.

        memory.max 등은 제한 미설정 시 "max" 문자열이 들어오므로
        int 변환 실패 시 0(=무제한)으로 처리한다.
        """
        try:
            file_path = cgroup_path / filename
            if file_path.exists():
                return int(file_path.read_text().strip())
        except (ValueError, FileNotFoundError):
            pass
        return 0

    def _read_cpu_usage_ns(self, cgroup_path: Path) -> int:
        """cpu.stat 의 usage_usec(키-값 멀티라인)을 파싱하여 ns로 반환.

        cpu.stat 은 'usage_usec 12345' 형태의 여러 줄이라 int() 직접 변환은
        항상 실패한다(이전 버그: cpu_usage_ns 항상 0).
        """
        try:
            file_path = cgroup_path / "cpu.stat"
            if file_path.exists():
                for line in file_path.read_text().splitlines():
                    if line.startswith("usage_usec"):
                        return int(line.split()[1]) * 1000  # usec -> ns
        except (ValueError, FileNotFoundError, IndexError):
            pass
        return 0

    def _get_container_cgroups(self) -> list:
        """컨테이너 cgroup 목록 가져오기"""
        containers = []

        try:
            # Docker 컨테이너 찾기
            docker_path = self.cgroup_base / "system.slice"
            if docker_path.exists():
                for item in docker_path.iterdir():
                    if item.is_dir() and "docker-" in item.name:
                        containers.append({
                            'type': 'docker',
                            'id': item.name.replace('docker-', '').replace('.scope', ''),
                            'path': item
                        })

            # Kubernetes Pod 찾기
            kubepods_path = self.cgroup_base / "kubepods"
            if kubepods_path.exists():
                for pod_dir in kubepods_path.rglob("pod*"):
                    if pod_dir.is_dir():
                        containers.append({
                            'type': 'kubernetes',
                            'pod_id': pod_dir.name,
                            'path': pod_dir
                        })

        except Exception as e:
            logger.error("container_discovery_error", error=str(e))

        return containers

    async def collect(self) -> Dict[str, Any]:
        """컨테이너 메트릭 수집"""
        metrics = []

        try:
            containers = self._get_container_cgroups()

            for container in containers:
                cgroup_path = container['path']

                # CPU 사용량 (cpu.stat의 usage_usec → ns)
                cpu_usage = self._read_cpu_usage_ns(cgroup_path)

                # 메모리 사용량
                memory_current = self._read_cgroup_value(
                    cgroup_path, "memory.current"
                )
                memory_limit = self._read_cgroup_value(
                    cgroup_path, "memory.max"
                )

                # 네트워크 통계 (namespace에서)
                # 실제 구현은 더 복잡할 수 있음

                metric = {
                    "measurement": "container_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "container_type": container['type'],
                        "container_id": container.get('id', container.get('pod_id', 'unknown'))[:12]
                    },
                    "fields": {
                        "cpu_usage_ns": cpu_usage,
                        "memory_usage_bytes": memory_current,
                        "memory_limit_bytes": memory_limit,
                        "memory_usage_percent": (memory_current / memory_limit * 100) if memory_limit > 0 else 0
                    },
                    "timestamp": int(time.time() * 1e9)
                }

                metrics.append(metric)

            logger.debug("container_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("container_collect_error", error=str(e))

        return {"type": "container", "metrics": metrics}