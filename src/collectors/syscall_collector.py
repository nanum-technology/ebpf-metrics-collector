# src/collectors/syscall_collector.py

import time
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class SyscallCollector(BaseCollector):
    """시스템 콜 메트릭 수집기"""

    def get_ebpf_program(self) -> str:
        return """
#include <uapi/linux/ptrace.h>

// 시스템 콜 통계 구조체
struct syscall_stats_t {
    u64 count;
    u64 total_latency_ns;
    u64 min_latency_ns;
    u64 max_latency_ns;
    u64 error_count;
};

// Key: syscall_id
BPF_HASH(syscall_stats, u32, struct syscall_stats_t);
BPF_HASH(syscall_start, u64, u64, 65536);  // pid_tid -> start_time (enter 누수 대비 여유)

// 시스템 콜 진입
TRACEPOINT_PROBE(raw_syscalls, sys_enter) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    u64 ts = bpf_ktime_get_ns();

    syscall_start.update(&pid_tid, &ts);

    return 0;
}

// 시스템 콜 종료
TRACEPOINT_PROBE(raw_syscalls, sys_exit) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    u32 syscall_id = args->id;
    long ret = args->ret;

    // 카운트/에러는 sys_enter 기록 유무와 무관하게 무조건 집계한다.
    // (이전 버그: syscall_start lookup 미스 시 early-return 되어 부하가 높을 때
    //  카운트가 통째로 누락됐음 → 시간대별 들쭉날쭉/과소집계의 원인)
    struct syscall_stats_t *stats, zero = {};
    stats = syscall_stats.lookup_or_init(&syscall_id, &zero);
    if (stats) {
        stats->count++;
        if (ret < 0) {
            stats->error_count++;
        }

        // latency는 sys_enter 시작시각이 있을 때만 best-effort로 계산
        u64 *tsp = syscall_start.lookup(&pid_tid);
        if (tsp) {
            u64 latency = bpf_ktime_get_ns() - *tsp;
            stats->total_latency_ns += latency;
            if (stats->min_latency_ns == 0 || latency < stats->min_latency_ns) {
                stats->min_latency_ns = latency;
            }
            if (latency > stats->max_latency_ns) {
                stats->max_latency_ns = latency;
            }
            syscall_start.delete(&pid_tid);
        }
    }

    return 0;
}
"""

    # 주요 시스템 콜 매핑
    SYSCALL_NAMES = {
        0: "read",
        1: "write",
        2: "open",
        3: "close",
        4: "stat",
        5: "fstat",
        9: "mmap",
        10: "mprotect",
        11: "munmap",
        13: "rt_sigaction",
        14: "rt_sigprocmask",
        16: "ioctl",
        21: "access",
        32: "dup",
        33: "dup2",
        39: "getpid",
        56: "clone",
        57: "fork",
        59: "execve",
        60: "exit",
        61: "wait4",
        62: "kill",
        72: "fcntl",
        78: "getdents",
        79: "getcwd",
        80: "chdir",
        82: "rename",
        83: "mkdir",
        84: "rmdir",
        85: "creat",
        86: "link",
        87: "unlink",
        88: "symlink",
        89: "readlink",
        90: "chmod",
        257: "openat",
        262: "newfstatat",
        # 더 많은 시스템 콜 추가 가능
    }

    async def collect(self) -> Dict[str, Any]:
        """시스템 콜 메트릭 수집"""
        metrics = []

        try:
            if self.bpf is None:
                return {"type": "syscall", "metrics": metrics}

            stats = self.bpf["syscall_stats"]

            for k, v in stats.items():
                syscall_id = k.value
                syscall_name = self.SYSCALL_NAMES.get(syscall_id, f"syscall_{syscall_id}")

                if v.count == 0:
                    continue

                avg_latency = v.total_latency_ns / v.count if v.count > 0 else 0

                metrics.append({
                    "measurement": "syscall_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "syscall_name": syscall_name,
                        "syscall_id": str(syscall_id)
                    },
                    "fields": {
                        "call_count": v.count,
                        "total_latency_us": v.total_latency_ns / 1000,
                        "avg_latency_us": avg_latency / 1000,
                        "min_latency_us": v.min_latency_ns / 1000,
                        "max_latency_us": v.max_latency_ns / 1000,
                        "error_count": v.error_count
                    },
                    "timestamp": int(time.time() * 1e9)
                })

            stats.clear()

            logger.debug("syscall_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("syscall_collect_error", error=str(e))

        return {"type": "syscall", "metrics": metrics}