# src/collectors/memory_collector.py

import time
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class MemoryCollector(BaseCollector):
    """메모리 메트릭 수집기"""

    def get_ebpf_program(self) -> str:
        return self.proc_key_define() + """
#include <uapi/linux/ptrace.h>
#include <linux/mm.h>
#include <linux/sched.h>

// 메모리 할당 정보 구조체
struct alloc_info_t {
    u64 timestamp;
    u32 pid;
    u64 size;
    u64 ptr;
    char comm[TASK_COMM_LEN];
    int stack_id;
};

// 메모리 통계 구조체
struct mem_stats_t {
    u64 kmalloc_count;
    u64 kfree_count;
    u64 kmalloc_bytes;
    u64 page_faults;
    u64 oom_count;
};

// 집계 키 — 기본은 프로세스 이름(comm). KEY_BY_PID 가 정의되면 pid (2026-10 이전 동작)
// pid 는 재사용되며 계속 바뀌어 하루 60만 시계열을 만들었다 (2026-10)
struct proc_key_t {
#ifdef KEY_BY_PID
    u32 pid;
#else
    char comm[TASK_COMM_LEN];
#endif
};
static inline void proc_key(struct proc_key_t *k) {
#ifdef KEY_BY_PID
    k->pid = bpf_get_current_pid_tgid() >> 32;
#else
    bpf_get_current_comm(&k->comm, sizeof(k->comm));
#endif
}

BPF_HASH(mem_stats, struct proc_key_t, struct mem_stats_t);
BPF_HASH(allocs, u64, u64);  // ptr -> size
BPF_STACK_TRACE(stack_traces, 10240);
BPF_PERF_OUTPUT(mem_events);

// kmalloc 추적
int trace_kmalloc(struct pt_regs *ctx, size_t size) {
    u32 pid = bpf_get_current_pid_tgid() >> 32;
    struct proc_key_t key = {};
    proc_key(&key);

    struct mem_stats_t *stats, zero = {};
    stats = mem_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->kmalloc_count++;
        stats->kmalloc_bytes += size;
    }

    struct alloc_info_t info = {};
    info.timestamp = bpf_ktime_get_ns();
    info.pid = pid;
    info.size = size;
    info.stack_id = stack_traces.get_stackid(ctx, BPF_F_REUSE_STACKID);
    bpf_get_current_comm(&info.comm, sizeof(info.comm));

    mem_events.perf_submit(ctx, &info, sizeof(info));

    return 0;
}

int trace_kmalloc_ret(struct pt_regs *ctx) {
    u64 ptr = PT_REGS_RC(ctx);
    struct proc_key_t key = {};
    proc_key(&key);

    struct mem_stats_t *stats = mem_stats.lookup(&key);
    if (stats && ptr != 0) {
        allocs.update(&ptr, &stats->kmalloc_bytes);
    }

    return 0;
}

// kfree 추적
int trace_kfree(struct pt_regs *ctx, void *ptr) {
    struct proc_key_t key = {};
    proc_key(&key);
    u64 addr = (u64)ptr;

    struct mem_stats_t *stats, zero = {};
    stats = mem_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->kfree_count++;
    }

    // 할당 기록 삭제
    allocs.delete(&addr);

    return 0;
}

// 페이지 폴트 추적
TRACEPOINT_PROBE(exceptions, page_fault_user) {
    struct proc_key_t key = {};
    proc_key(&key);

    struct mem_stats_t *stats, zero = {};
    stats = mem_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->page_faults++;
    }

    return 0;
}

// OOM (Out of Memory) 이벤트 추적
TRACEPOINT_PROBE(oom, mark_victim) {
    struct proc_key_t key = {};
    proc_key(&key);

    struct mem_stats_t *stats, zero = {};
    stats = mem_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->oom_count++;
    }

    return 0;
}

// Slab 메모리 추적
TRACEPOINT_PROBE(kmem, kmem_cache_alloc) {
    struct proc_key_t key = {};
    proc_key(&key);

    struct mem_stats_t *stats, zero = {};
    stats = mem_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->kmalloc_count++;
        stats->kmalloc_bytes += args->bytes_alloc;
    }

    return 0;
}
"""

    async def collect(self) -> Dict[str, Any]:
        """메모리 메트릭 수집"""
        import psutil

        metrics = []

        try:
            # eBPF 통계 수집
            if self.bpf is not None:
                stats = self.bpf["mem_stats"]

                for k, v in stats.items():
                    metric = {
                        "measurement": "memory_metrics",
                        "tags": {
                            **self.get_common_tags(),
                            **self.proc_tags(k),
                            "memory_type": "kernel"
                        },
                        "fields": {
                            "kmalloc_count": v.kmalloc_count,
                            "kfree_count": v.kfree_count,
                            "kmalloc_bytes": v.kmalloc_bytes,
                            "page_faults": v.page_faults,
                            "oom_count": v.oom_count
                        },
                        "timestamp": int(time.time() * 1e9)
                    }
                    metrics.append(metric)

                # 통계 초기화
                stats.clear()

            # 시스템 전체 메모리 정보 (psutil 사용)
            vm = psutil.virtual_memory()
            swap = psutil.swap_memory()

            # Physical Memory
            metrics.append({
                "measurement": "memory_metrics",
                "tags": {
                    **self.get_common_tags(),
                    "memory_type": "physical"
                },
                "fields": {
                    "total_bytes": vm.total,
                    "used_bytes": vm.used,
                    "free_bytes": vm.free,
                    "available_bytes": vm.available,
                    "usage_percent": vm.percent,
                    "buffers_bytes": getattr(vm, 'buffers', 0),
                    "cached_bytes": getattr(vm, 'cached', 0)
                },
                "timestamp": int(time.time() * 1e9)
            })

            # Swap Memory
            metrics.append({
                "measurement": "memory_metrics",
                "tags": {
                    **self.get_common_tags(),
                    "memory_type": "swap"
                },
                "fields": {
                    "total_bytes": swap.total,
                    "used_bytes": swap.used,
                    "free_bytes": swap.free,
                    "usage_percent": swap.percent,
                    "sin_bytes": swap.sin,  # swap in
                    "sout_bytes": swap.sout  # swap out
                },
                "timestamp": int(time.time() * 1e9)
            })

            logger.debug("memory_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("memory_collect_error", error=str(e))

        return {"type": "memory", "metrics": metrics}