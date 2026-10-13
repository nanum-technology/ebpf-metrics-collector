# src/collectors/base_collector.py

from abc import ABC, abstractmethod
from bcc import BPF
import structlog
from typing import Dict, Any, Optional

logger = structlog.get_logger()


class BaseCollector(ABC):
    """eBPF 수집기 기본 클래스"""

    def __init__(self, config: Dict[str, Any], node_info: Dict[str, str]):
        """
        Args:
            config: 수집기 설정
            node_info: 노드 정보 (node_id, hostname, ip)
        """
        self.config = config
        self.node_info = node_info
        self.bpf: Optional[BPF] = None
        self.is_running = False

    @abstractmethod
    def get_ebpf_program(self) -> str:
        """eBPF C 프로그램 반환"""
        pass

    @abstractmethod
    async def collect(self) -> Dict[str, Any]:
        """메트릭 수집"""
        pass

    def load_ebpf(self):
        """eBPF 프로그램 로드"""
        try:
            program = self.get_ebpf_program()
            self.bpf = BPF(text=program, cflags=["-w"])
            logger.info("ebpf_loaded", collector=self.__class__.__name__)
        except Exception as e:
            logger.error("ebpf_load_failed",
                         collector=self.__class__.__name__,
                         error=str(e))
            raise

    def start(self):
        """수집 시작"""
        self.load_ebpf()
        self.is_running = True
        logger.info("collector_started", collector=self.__class__.__name__)

    def stop(self):
        """수집 중지"""
        self.is_running = False
        if self.bpf is not None:
            self.bpf.cleanup()
        logger.info("collector_stopped", collector=self.__class__.__name__)

    def get_common_tags(self) -> Dict[str, str]:
        """공통 태그 반환"""
        return {
            "node_id": self.node_info.get('node_id', ''),
            "hostname": self.node_info.get('hostname', ''),
            "ip": self.node_info.get('ip', '')
        }