# src/storage/influxdb_schema.py

"""
InfluxDB 2.x 데이터 구조 — measurement / tags / fields 의 단일 출처

Bucket: ebpf_metrics

Measurements:
1. network_metrics     5. filesystem_metrics
2. syscall_metrics     6. process_metrics
3. cpu_metrics         7. container_metrics
4. memory_metrics      8. gpu_metrics

규칙 (2026-10-13 — 스키마 검증 레이어 도입, REQ-B-1)
  - 이 표는 문서가 아니라 **검증 기준**이다. 수집기가 여기 없는 태그·필드를 내보내면
    `validate_points()` 가 설정(`influxdb.schema_mode`)에 따라 경고하거나 떼어낸다.
  - 필드 타입 표기(int64/float64/boolean/string)는 **수집기가 실제로 내보내는 타입**이다.
    InfluxDB 는 같은 필드에 다른 타입이 들어오면 쓰기를 거부하므로, 타입을 바꾸려면
    필드 이름을 새로 만들어야 한다. 검증기는 타입을 바꾸지 않고 불일치만 기록한다.
  - 수집기에 태그·필드를 추가하면 **같은 커밋에서 이 표에 넣는다.**
"""

import structlog

logger = structlog.get_logger()

# 모든 measurement 공통 태그
COMMON_TAGS = {
    "node_id": "노드 식별자 (--node-id, master=agent-001 / slave=agent-002)",
    "hostname": "호스트명",
    "ip": "노드 IP",
}

INFLUXDB_SCHEMA = {
    # 1. 네트워크 — 연결 4-tuple 단위 집계 + 노드 단위 요약 1건
    "network_metrics": {
        "tags": {
            **COMMON_TAGS,
            "protocol": "TCP / UDP / ICMP / ALL(노드 요약)",
            "direction": "in / out",
            "src_ip": "출발지 IP (요약 레코드는 빈 값)",
            "dst_ip": "목적지 IP",
            "sport": "로컬 포트 (ICMP·요약은 빈 값)",
            "dport": "원격 포트",
        },
        "fields": {
            "packet_count": "int64 - 패킷 수 (TCP 는 sendmsg/cleanup_rbuf 호출 수)",
            "byte_count": "int64 - 바이트 수",
            "error_count": "int64 - 에러 수 (현재 0)",
            "drop_count": "int64 - 드롭 수 (kfree_skb, 노드 요약 레코드)",
            "retransmit_count": "int64 - TCP 재전송 수 (tracepoint tcp:tcp_retransmit_skb)",
            "rtt_ms": "float64 - TCP smoothed RTT (tcp_sock.srtt_us, 마지막 관측값)",
            "loss_rate": "float64 - 재전송 / 송신 패킷 (0~1, out 레코드)",
            "inbound_pps": "float64 - 수신 패킷/초 (노드 요약)",
            "unique_src_count": "int64 - 수신 출발지 IP 종류 수 (노드 요약)",
            "ddos_detected": "boolean - 임계 초과 여부 (노드 요약, 규칙 기반)",
        },
    },

    # 2. 시스템 콜
    "syscall_metrics": {
        "tags": {
            **COMMON_TAGS,
            "syscall_name": "시스템 콜 이름 (매핑 없으면 syscall_<id>)",
            "syscall_id": "시스템 콜 번호",
        },
        "fields": {
            "call_count": "int64 - 호출 횟수",
            "total_latency_us": "float64 - 총 지연시간 (us)",
            "avg_latency_us": "float64 - 평균 지연시간 (us)",
            "min_latency_us": "float64 - 최소 지연시간 (us)",
            "max_latency_us": "float64 - 최대 지연시간 (us)",
            "error_count": "int64 - 음수 반환 횟수",
        },
    },

    # 3. CPU — 코어 단위
    "cpu_metrics": {
        "tags": {
            **COMMON_TAGS,
            "cpu_id": "CPU 코어 번호",
        },
        "fields": {
            "user_time_ms": "int64 - 주기 동안 user 모드 시간 (/proc/stat 델타)",
            "system_time_ms": "int64 - 주기 동안 system 모드 시간",
            "idle_time_ms": "int64 - 주기 동안 유휴 시간",
            "iowait_time_ms": "int64 - 주기 동안 I/O 대기 시간",
            "usage_percent": "float64 - 주기 동안 사용률 (%)",
            "context_switches": "int64 - 컨텍스트 스위치 횟수 (kprobe finish_task_switch)",
            "irq_count": "int64 - 하드 IRQ 횟수 (tracepoint irq:irq_handler_entry)",
            "softirq_count": "int64 - SoftIRQ 횟수 (tracepoint irq:softirq_entry)",
        },
    },

    # 4. 메모리 — eBPF(프로세스 이름 단위 kernel) + psutil(physical/swap)
    "memory_metrics": {
        "tags": {
            **COMMON_TAGS,
            "memory_type": "kernel / physical / swap",
            "process_name": "프로세스 이름 (memory_type=kernel 만, 기본 집계 키)",
            "pid": "프로세스 ID (memory_type=kernel 만, aggregate_by: pid 일 때)",
        },
        "fields": {
            "kmalloc_count": "int64 - kmalloc + kmem_cache_alloc 횟수",
            "kfree_count": "int64 - kfree 횟수",
            "kmalloc_bytes": "int64 - 할당 바이트",
            "page_faults": "int64 - 사용자 페이지 폴트 횟수",
            "oom_count": "int64 - OOM victim 지정 횟수",
            "total_bytes": "int64 - 총 메모리",
            "used_bytes": "int64 - 사용 메모리",
            "free_bytes": "int64 - 여유 메모리",
            "available_bytes": "int64 - 가용 메모리 (physical)",
            "usage_percent": "float64 - 사용률 (%)",
            "buffers_bytes": "int64 - 버퍼 (physical)",
            "cached_bytes": "int64 - 캐시 (physical)",
            "sin_bytes": "int64 - swap in",
            "sout_bytes": "int64 - swap out",
        },
    },

    # 5. 파일시스템 — eBPF(프로세스 이름 단위 VFS) + psutil(마운트 사용량·디스크 I/O) + 블록 레이어
    "filesystem_metrics": {
        "tags": {
            **COMMON_TAGS,
            "process_name": "프로세스 이름 (VFS 레코드, 기본 집계 키)",
            "pid": "프로세스 ID (VFS 레코드, aggregate_by: pid 일 때)",
            "operation": "read / write (VFS) · block (블록 레이어)",
            "mount_point": "마운트 지점 (사용량 레코드)",
            "filesystem_type": "파일시스템 타입 (사용량 레코드)",
            "device": "장치 (사용량·디스크 I/O·블록 레코드, all = 전체 합계)",
        },
        "fields": {
            "io_operations": "int64 - VFS I/O 횟수",
            "bytes_transferred": "int64 - VFS 전송 바이트",
            "io_latency_us": "float64 - VFS 평균 지연 (us)",
            "open_count": "int64 - open 횟수",
            "close_count": "int64 - close 횟수",
            "unlink_count": "int64 - unlink/unlinkat 횟수",
            "total_bytes": "int64 - 마운트 총 용량",
            "used_bytes": "int64 - 마운트 사용량",
            "free_bytes": "int64 - 마운트 여유",
            "usage_percent": "float64 - 마운트 사용률 (%)",
            "read_count": "int64 - 디스크 읽기 횟수 (psutil, 누적)",
            "write_count": "int64 - 디스크 쓰기 횟수 (누적)",
            "read_bytes": "int64 - 디스크 읽기 바이트 (누적)",
            "write_bytes": "int64 - 디스크 쓰기 바이트 (누적)",
            "read_time_ms": "int64 - 디스크 읽기 시간 (누적)",
            "write_time_ms": "int64 - 디스크 쓰기 시간 (누적)",
            "block_read_ops": "int64 - 블록 요청 발행(읽기) 횟수 (tracepoint block:block_rq_issue)",
            "block_write_ops": "int64 - 블록 요청 발행(쓰기) 횟수",
            "block_read_bytes": "int64 - 블록 읽기 바이트",
            "block_write_bytes": "int64 - 블록 쓰기 바이트",
            "block_latency_us": "float64 - 블록 요청 발행→완료 평균 지연 (us)",
        },
    },

    # 6. 프로세스/스레드
    "process_metrics": {
        "tags": {
            **COMMON_TAGS,
            "pid": "프로세스 ID (summary = 노드 요약)",
            "process_name": "프로세스 이름 (system = 노드 요약)",
            "state": "psutil 상태 (all = 노드 요약)",
        },
        "fields": {
            "fork_count": "int64 - fork 횟수",
            "exec_count": "int64 - exec 횟수",
            "exit_count": "int64 - exit 횟수",
            "schedule_count": "int64 - sched_switch 관여 횟수",
            "state_changes": "int64 - 상태 변경 횟수 (현재 0)",
            "thread_count": "int64 - 스레드 수",
            "cpu_usage_percent": "float64 - CPU 사용률",
            "memory_usage_bytes": "int64 - RSS",
            "total_count": "int64 - 전체 프로세스 수 (요약)",
            "running_count": "int64 - running (요약)",
            "sleeping_count": "int64 - sleeping (요약)",
            "zombie_count": "int64 - zombie (요약)",
        },
    },

    # 7. 컨테이너 / Kubernetes (cgroup v2)
    "container_metrics": {
        "tags": {
            **COMMON_TAGS,
            "container_type": "docker / kubernetes",
            "container_id": "컨테이너 ID 또는 Pod ID 앞 12자",
        },
        "fields": {
            "cpu_usage_ns": "int64 - cpu.stat usage_usec × 1000 (누적)",
            "memory_usage_bytes": "int64 - memory.current",
            "memory_limit_bytes": "int64 - memory.max (max = 0)",
            "memory_usage_percent": "float64 - 사용량/제한 (%)",
        },
    },

    # 8. GPU (pynvml — eBPF 아님, config 기본 비활성)  REQ-B-4
    "gpu_metrics": {
        "tags": {
            **COMMON_TAGS,
            "gpu_index": "GPU 인덱스",
            "gpu_name": "GPU 모델명",
            "gpu_uuid": "GPU UUID",
        },
        "fields": {
            "gpu_utilization_pct": "float64 - 코어 사용률 (%)",
            "mem_utilization_pct": "float64 - 메모리 컨트롤러 사용률 (%)",
            "mem_used_bytes": "int64 - VRAM 사용",
            "mem_free_bytes": "int64 - VRAM 여유",
            "mem_total_bytes": "int64 - VRAM 전체",
            "temperature_celsius": "float64 - 온도",
            "power_watts": "float64 - 전력",
            "fan_speed_pct": "float64 - 팬 속도 (%)",
            "clock_sm_mhz": "float64 - SM 클럭",
            "clock_mem_mhz": "float64 - 메모리 클럭",
        },
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# 검증 레이어 (REQ-B-1)
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_MODES = ("off", "warn", "strict")

_PY_TYPES = {
    "int64": (int,),
    "float64": (float, int),   # int 가 float 필드에 들어오는 것은 InfluxDB 가 받는다 (반대는 거부)
    "boolean": (bool,),
    "string": (str,),
}

# 같은 경고를 주기마다 반복하지 않기 위한 기억 — (measurement, kind, name)
_warned = set()


def _declared_type(desc: str) -> str:
    """필드 설명 문자열 앞의 타입 토큰 (예: 'int64 - 패킷 수' → 'int64')."""
    return desc.split(" ", 1)[0].strip().lower()


def _warn_once(key, event, **kw):
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(event, **kw)


def validate_points(points, mode: str = "warn", collector: str = ""):
    """수집기가 넘긴 포인트 목록을 스키마와 대조한다.

    Args:
        points: [{measurement, tags, fields, timestamp}, …]
        mode:   off    — 아무것도 하지 않는다
                warn   — 어긋남을 기록만 하고 그대로 통과 (기본)
                strict — 스키마에 없는 measurement 는 포인트를 버리고,
                         없는 태그·필드는 떼어낸다. 필드가 하나도 안 남으면 포인트를 버린다.
                         타입 불일치는 strict 에서도 기록만 한다 (바꾸면 기존 시리즈와 충돌).
    Returns:
        통과한 포인트 목록 (warn/off 는 입력 그대로)
    """
    if mode == "off" or not points:
        return points
    if mode not in SCHEMA_MODES:
        _warn_once(("mode", mode), "schema_mode_unknown", mode=mode)
        return points

    out = []
    dropped_points = 0
    for p in points:
        m = p.get("measurement")
        sch = INFLUXDB_SCHEMA.get(m)
        if sch is None:
            _warn_once((m, "measurement", ""), "schema_unknown_measurement",
                       measurement=m, collector=collector, mode=mode)
            if mode == "strict":
                dropped_points += 1
                continue
            out.append(p)
            continue

        tags = p.get("tags", {}) or {}
        fields = p.get("fields", {}) or {}

        bad_tags = [k for k in tags if k not in sch["tags"]]
        bad_fields = [k for k in fields if k not in sch["fields"]]
        for k in bad_tags:
            _warn_once((m, "tag", k), "schema_unknown_tag", measurement=m, tag=k, collector=collector, mode=mode)
        for k in bad_fields:
            _warn_once((m, "field", k), "schema_unknown_field", measurement=m, field=k, collector=collector, mode=mode)

        # 타입 점검 — 기록만
        for k, v in fields.items():
            if k in sch["fields"] and v is not None:
                want = _PY_TYPES.get(_declared_type(sch["fields"][k]))
                if want and not isinstance(v, want) or (isinstance(v, bool) and want and bool not in want):
                    _warn_once((m, "type", k), "schema_type_mismatch",
                               measurement=m, field=k, declared=_declared_type(sch["fields"][k]),
                               actual=type(v).__name__, collector=collector)

        if mode == "strict" and (bad_tags or bad_fields):
            p = dict(p)
            p["tags"] = {k: v for k, v in tags.items() if k in sch["tags"]}
            p["fields"] = {k: v for k, v in fields.items() if k in sch["fields"]}
            if not p["fields"]:
                dropped_points += 1
                continue
        out.append(p)

    if dropped_points:
        logger.warning("schema_points_dropped", collector=collector, dropped=dropped_points, kept=len(out))
    return out if mode == "strict" else points
