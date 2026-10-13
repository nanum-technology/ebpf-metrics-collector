# src/collectors/process_collector.py

import time
from typing import Dict, Any
from .base_collector import BaseCollector
import psutil
import structlog

logger = structlog.get_logger()


class ProcessCollector(BaseCollector):
    """프로세스/스레드 메트릭 수집기"""

    def get_ebpf_program(self) -> str:
        return """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

// 프로세스 이벤트 구조체
struct proc_event_t {
    u64 timestamp;
    u32 pid;
    u32 ppid;
    char comm[TASK_COMM_LEN];
    u8 event_type;  // 0: fork, 1: exec, 2: exit
};

// 프로세스 통계 구조체
struct proc_stats_t {
    u64 fork_count;
    u64 exec_count;
    u64 exit_count;
    u64 thread_count;
    u64 schedule_count;
    u64 state_changes;
};

BPF_HASH(proc_stats, u32, struct proc_stats_t);
BPF_PERF_OUTPUT(proc_events);

// fork 이벤트 추적
TRACEPOINT_PROBE(sched, sched_process_fork) {
    u32 pid = args->parent_pid;

    struct proc_stats_t *stats, zero = {};
    stats = proc_stats.lookup_or_init(&pid, &zero);
    if (stats) {
        stats->fork_count++;
    }

    struct proc_event_t event = {};
    event.timestamp = bpf_ktime_get_ns();
    event.pid = args->child_pid;
    event.ppid = pid;
    event.event_type = 0;  // fork
    bpf_probe_read_kernel_str(&event.comm, sizeof(event.comm), args->child_comm);

    proc_events.perf_submit(args, &event, sizeof(event));

    return 0;
}

// exec 이벤트 추적
TRACEPOINT_PROBE(sched, sched_process_exec) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;

    struct proc_stats_t *stats, zero = {};
    stats = proc_stats.lookup_or_init(&pid, &zero);
    if (stats) {
        stats->exec_count++;
    }

    struct proc_event_t event = {};
    event.timestamp = bpf_ktime_get_ns();
    event.pid = pid;
    event.event_type = 1;  // exec
    bpf_get_current_comm(&event.comm, sizeof(event.comm));

    proc_events.perf_submit(args, &event, sizeof(event));

    return 0;
}

// exit 이벤트 추적
TRACEPOINT_PROBE(sched, sched_process_exit) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;

    struct proc_stats_t *stats = proc_stats.lookup(&pid);
    if (stats) {
        stats->exit_count++;
    }

    struct proc_event_t event = {};
    event.timestamp = bpf_ktime_get_ns();
    event.pid = pid;
    event.event_type = 2;  // exit
    bpf_get_current_comm(&event.comm, sizeof(event.comm));

    proc_events.perf_submit(args, &event, sizeof(event));

    return 0;
}

// 스케줄링 이벤트 추적
TRACEPOINT_PROBE(sched, sched_switch) {
    u32 prev_pid = args->prev_pid;
    u32 next_pid = args->next_pid;

    struct proc_stats_t *stats, zero = {};

    // 이전 프로세스 통계
    stats = proc_stats.lookup_or_init(&prev_pid, &zero);
    if (stats) {
        stats->schedule_count++;
    }

    // 다음 프로세스 통계
    stats = proc_stats.lookup_or_init(&next_pid, &zero);
    if (stats) {
        stats->schedule_count++;
    }

    return 0;
}
"""

    async def collect(self) -> Dict[str, Any]:
        """프로세스 메트릭 수집"""
        metrics = []

        try:
            # eBPF 통계 수집
            if self.bpf is not None:
                stats = self.bpf["proc_stats"]

                for k, v in stats.items():
                    pid = k.value

                    try:
                        # psutil로 프로세스 정보 가져오기
                        proc = psutil.Process(pid)

                        metrics.append({
                            "measurement": "process_metrics",
                            "tags": {
                                **self.get_common_tags(),
                                "pid": str(pid),
                                "process_name": proc.name(),
                                "state": proc.status()
                            },
                            "fields": {
                                "fork_count": v.fork_count,
                                "exec_count": v.exec_count,
                                "exit_count": v.exit_count,
                                "schedule_count": v.schedule_count,
                                "state_changes": v.state_changes,
                                "thread_count": proc.num_threads(),
                                "cpu_usage_percent": proc.cpu_percent(),
                                "memory_usage_bytes": proc.memory_info().rss
                            },
                            "timestamp": int(time.time() * 1e9)
                        })
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue

                stats.clear()

            # 전체 프로세스 요약 정보
            all_procs = list(psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'status']))

            total_procs = len(all_procs)
            running_procs = sum(1 for p in all_procs if p.info['status'] == psutil.STATUS_RUNNING)
            sleeping_procs = sum(1 for p in all_procs if p.info['status'] == psutil.STATUS_SLEEPING)
            zombie_procs = sum(1 for p in all_procs if p.info['status'] == psutil.STATUS_ZOMBIE)

            metrics.append({
                "measurement": "process_metrics",
                "tags": {
                    **self.get_common_tags(),
                    "pid": "summary",
                    "process_name": "system",
                    "state": "all"
                },
                "fields": {
                    "total_count": total_procs,
                    "running_count": running_procs,
                    "sleeping_count": sleeping_procs,
                    "zombie_count": zombie_procs
                },
                "timestamp": int(time.time() * 1e9)
            })

            logger.debug("process_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("process_collect_error", error=str(e))

        return {"type": "process", "metrics": metrics}