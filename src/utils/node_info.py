# src/utils/node_info.py

import socket
import platform
import psutil
from typing import Dict
import structlog

logger = structlog.get_logger()


def get_node_info(node_id: str = None) -> Dict[str, str]:
    """
    노드 정보 수집

    Args:
        node_id: 노드 식별자 (선택사항, 없으면 자동 생성)

    Returns:
        노드 정보 딕셔너리
    """
    try:
        hostname = socket.gethostname()

        # IP 주소 가져오기
        try:
            # 외부 네트워크 인터페이스 IP
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip_address = s.getsockname()[0]
            s.close()
        except Exception:
            # 로컬호스트로 폴백
            ip_address = socket.gethostbyname(hostname)

        # node_id가 없으면 hostname 기반으로 생성
        if not node_id:
            node_id = f"{hostname}-{ip_address.replace('.', '-')}"

        node_info = {
            'node_id': node_id,
            'hostname': hostname,
            'ip': ip_address,
            'platform': platform.system(),
            'platform_release': platform.release(),
            'platform_version': platform.version(),
            'architecture': platform.machine(),
            'processor': platform.processor(),
            'cpu_count': psutil.cpu_count(logical=True),
            'cpu_count_physical': psutil.cpu_count(logical=False),
            'total_memory_gb': round(psutil.virtual_memory().total / (1024 ** 3), 2)
        }

        logger.info("node_info_collected", node_id=node_id, hostname=hostname)
        return node_info

    except Exception as e:
        logger.error("node_info_error", error=str(e))
        # 최소한의 정보라도 반환
        return {
            'node_id': node_id or 'unknown',
            'hostname': socket.gethostname(),
            'ip': '127.0.0.1'
        }


def get_network_interfaces() -> Dict[str, Dict]:
    """
    네트워크 인터페이스 정보 수집

    Returns:
        인터페이스별 정보 딕셔너리
    """
    interfaces = {}

    try:
        net_if_addrs = psutil.net_if_addrs()
        net_if_stats = psutil.net_if_stats()

        for interface_name, addresses in net_if_addrs.items():
            # 루프백 제외
            if interface_name == 'lo':
                continue

            interface_info = {
                'name': interface_name,
                'addresses': []
            }

            for addr in addresses:
                if addr.family == socket.AF_INET:  # IPv4
                    interface_info['addresses'].append({
                        'type': 'IPv4',
                        'address': addr.address,
                        'netmask': addr.netmask
                    })
                elif addr.family == socket.AF_INET6:  # IPv6
                    interface_info['addresses'].append({
                        'type': 'IPv6',
                        'address': addr.address,
                        'netmask': addr.netmask
                    })

            # 인터페이스 상태
            if interface_name in net_if_stats:
                stats = net_if_stats[interface_name]
                interface_info['is_up'] = stats.isup
                interface_info['speed_mbps'] = stats.speed
                interface_info['mtu'] = stats.mtu

            interfaces[interface_name] = interface_info

        return interfaces

    except Exception as e:
        logger.error("network_interface_error", error=str(e))
        return {}