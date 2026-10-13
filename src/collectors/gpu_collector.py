# src/collectors/gpu_collector.py

import time
from typing import Dict, Any, List
import structlog

logger = structlog.get_logger()


class GPUCollector:
    """NVIDIA GPU 메트릭 수집기 (pynvml 기반, eBPF 불필요)"""

    def __init__(self, config: Dict[str, Any], node_info: Dict[str, str]):
        self.config = config
        self.node_info = node_info
        self.is_running = False
        self._nvml_initialized = False

    def get_common_tags(self) -> Dict[str, str]:
        return {
            "node_id": self.node_info.get('node_id', ''),
            "hostname": self.node_info.get('hostname', ''),
            "ip": self.node_info.get('ip', '')
        }

    def start(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            self._nvml_initialized = True
            self.is_running = True
            gpu_count = pynvml.nvmlDeviceGetCount()
            logger.info("gpu_collector_started", gpu_count=gpu_count)
        except ImportError:
            logger.error("gpu_collector_start_failed",
                         error="pynvml not installed. Run: pip install nvidia-ml-py3")
            raise
        except Exception as e:
            logger.error("gpu_collector_start_failed", error=str(e))
            raise

    def stop(self):
        self.is_running = False
        if self._nvml_initialized:
            try:
                import pynvml
                pynvml.nvmlShutdown()
            except Exception:
                pass
        logger.info("gpu_collector_stopped")

    async def collect(self) -> Dict[str, Any]:
        """GPU 메트릭 수집"""
        metrics = []

        try:
            import pynvml
            gpu_count = pynvml.nvmlDeviceGetCount()

            for i in range(gpu_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)

                # GPU 이름 및 UUID
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode('utf-8')
                uuid = pynvml.nvmlDeviceGetUUID(handle)
                if isinstance(uuid, bytes):
                    uuid = uuid.decode('utf-8')

                # 사용률
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)

                # 메모리
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)

                # 온도
                temp = pynvml.nvmlDeviceGetTemperature(
                    handle, pynvml.NVML_TEMPERATURE_GPU)

                # 전력 사용량
                try:
                    power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
                    power_w = power_mw / 1000.0
                except pynvml.NVMLError:
                    power_w = 0.0

                # 팬 속도
                try:
                    fan_speed = pynvml.nvmlDeviceGetFanSpeed(handle)
                except pynvml.NVMLError:
                    fan_speed = 0

                # 클럭 속도
                try:
                    clock_sm = pynvml.nvmlDeviceGetClockInfo(
                        handle, pynvml.NVML_CLOCK_SM)
                    clock_mem = pynvml.nvmlDeviceGetClockInfo(
                        handle, pynvml.NVML_CLOCK_MEM)
                except pynvml.NVMLError:
                    clock_sm = 0
                    clock_mem = 0

                metric = {
                    "measurement": "gpu_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "gpu_index": str(i),
                        "gpu_name": name,
                        "gpu_uuid": uuid
                    },
                    "fields": {
                        "gpu_utilization_pct": float(util.gpu),
                        "mem_utilization_pct": float(util.memory),
                        "mem_used_bytes": mem_info.used,
                        "mem_free_bytes": mem_info.free,
                        "mem_total_bytes": mem_info.total,
                        "temperature_celsius": float(temp),
                        "power_watts": power_w,
                        "fan_speed_pct": float(fan_speed),
                        "clock_sm_mhz": float(clock_sm),
                        "clock_mem_mhz": float(clock_mem)
                    },
                    "timestamp": int(time.time() * 1e9)
                }
                metrics.append(metric)

        except Exception as e:
            logger.error("gpu_collect_error", error=str(e))

        return {"type": "gpu", "metrics": metrics}
