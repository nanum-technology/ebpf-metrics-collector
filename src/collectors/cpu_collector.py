# src/collectors/cpu_collector.py

import time
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class CPUCollector(BaseCollector):
    """CPU 메트릭 수집기 — 코어 단위

      eBPF  : 컨텍스트 스위치(kprobe finish_task_switch) · 하드 IRQ(tracepoint irq:irq_handler_entry)
              · SoftIRQ(tracepoint irq:softirq_entry)                                     (2026-10 추가)
      /proc : user/system/idle/iowait 시간과 사용률 — 주기 사이의 /proc/stat 델타          (2026-10 추가)
              eBPF 로는 모드별 CPU 시간을 직접 셀 수 없어(스케줄러 이벤트는 태스크 단위) 커널 집계값을 읽는다.
              이전에는 user_time_ms/system_time_ms 가 항상 0 이었다.
    """

    _CLK_TCK = 100  # USER_HZ — Linux x86 고정값 (os.sysconf 가 없는 환경 대비 기본)

    def __init__(self, config: Dict[str, Any], node_info: Dict[str, str]):
        super().__init__(config, node_info)
        self._prev_proc = None   # {cpu_id: (user, nice, system, idle, iowait, irq, softirq, steal)}
        try:
            import os
            self._CLK_TCK = os.sysconf("SC_CLK_TCK")
        except Exception:
            pass

    def load_ebpf(self):
        """eBPF 로드 후 sched_switch kprobe 명시적 attach.

        trace_sched_switch 는 함수형 프로브라 자동 attach 되지 않는다.
        커널 빌드에 따라 finish_task_switch 심볼이 .isra.N 으로 인라인되므로
        정규식으로 매칭하여 부착한다. (미부착 시 context_switches 항상 0)
        """
        super().load_ebpf()
        try:
            self.bpf.attach_kprobe(
                event_re=r"^finish_task_switch(\.isra\.\d+)?$",
                fn_name="trace_sched_switch",
            )
            logger.info("cpu_kprobe_attached")
        except Exception as e:
            logger.error("cpu_kprobe_attach_failed", error=str(e))

    def get_ebpf_program(self) -> str:
        return """
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

struct cpu_stats_t {
    u64 context_switches;
    u64 irq_count;
    u64 softirq_count;
};

BPF_HASH(cpu_stats, u32, struct cpu_stats_t);

// 컨텍스트 스위치 (finish_task_switch 는 전환이 끝난 CPU 에서 돈다)
int trace_sched_switch(struct pt_regs *ctx, struct task_struct *prev) {
    u32 cpu_id = bpf_get_smp_processor_id();
    struct cpu_stats_t *stats, zero = {};
    stats = cpu_stats.lookup_or_init(&cpu_id, &zero);
    if (stats) {
        stats->context_switches++;
    }
    return 0;
}

// 하드 IRQ
TRACEPOINT_PROBE(irq, irq_handler_entry) {
    u32 cpu_id = bpf_get_smp_processor_id();
    struct cpu_stats_t *stats, zero = {};
    stats = cpu_stats.lookup_or_init(&cpu_id, &zero);
    if (stats) {
        stats->irq_count++;
    }
    return 0;
}

// SoftIRQ
TRACEPOINT_PROBE(irq, softirq_entry) {
    u32 cpu_id = bpf_get_smp_processor_id();
    struct cpu_stats_t *stats, zero = {};
    stats = cpu_stats.lookup_or_init(&cpu_id, &zero);
    if (stats) {
        stats->softirq_count++;
    }
    return 0;
}
"""

    def _read_proc_stat(self) -> Dict[int, tuple]:
        """/proc/stat 의 cpuN 줄 → {cpu_id: (user, nice, system, idle, iowait, irq, softirq, steal)} (jiffies)."""
        out = {}
        try:
            with open("/proc/stat", "r") as f:
                for line in f:
                    if not line.startswith("cpu") or line.startswith("cpu "):
                        continue
                    parts = line.split()
                    try:
                        cpu_id = int(parts[0][3:])
                        vals = tuple(int(x) for x in parts[1:9])
                    except (ValueError, IndexError):
                        continue
                    if len(vals) == 8:
                        out[cpu_id] = vals
        except Exception as e:
            logger.error("cpu_proc_stat_read_error", error=str(e))
        return out

    def _proc_delta_fields(self, cur: Dict[int, tuple]) -> Dict[int, Dict[str, Any]]:
        """이전 주기 대비 델타를 ms 와 사용률로 환산. 첫 주기는 기준만 잡고 빈 값."""
        fields: Dict[int, Dict[str, Any]] = {}
        prev = self._prev_proc
        self._prev_proc = cur
        if not prev:
            return fields
        ms = 1000.0 / float(self._CLK_TCK)
        for cpu_id, now in cur.items():
            before = prev.get(cpu_id)
            if not before:
                continue
            d = [max(0, a - b) for a, b in zip(now, before)]
            user, nice, system, idle, iowait, irq, softirq, steal = d
            total = sum(d)
            busy = total - idle - iowait
            fields[cpu_id] = {
                "user_time_ms": int((user + nice) * ms),
                "system_time_ms": int((system + irq + softirq) * ms),
                "idle_time_ms": int(idle * ms),
                "iowait_time_ms": int(iowait * ms),
                "usage_percent": round((busy / total * 100.0), 2) if total > 0 else 0.0,
            }
        return fields

    async def collect(self) -> Dict[str, Any]:
        """CPU 메트릭 수집 — 코어마다 eBPF 카운터 + /proc/stat 델타를 한 레코드로"""
        metrics = []

        try:
            ebpf = {}
            if self.bpf is not None:
                stats = self.bpf["cpu_stats"]
                for k, v in stats.items():
                    ebpf[k.value] = {
                        "context_switches": v.context_switches,
                        "irq_count": v.irq_count,
                        "softirq_count": v.softirq_count,
                    }
                stats.clear()

            proc = self._proc_delta_fields(self._read_proc_stat())

            timestamp = int(time.time() * 1e9)
            for cpu_id in sorted(set(ebpf) | set(proc)):
                fields = {
                    "context_switches": 0,
                    "irq_count": 0,
                    "softirq_count": 0,
                }
                fields.update(ebpf.get(cpu_id, {}))
                fields.update(proc.get(cpu_id, {}))
                metrics.append({
                    "measurement": "cpu_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "cpu_id": str(cpu_id)
                    },
                    "fields": fields,
                    "timestamp": timestamp
                })

        except Exception as e:
            logger.error("cpu_collect_error", error=str(e))

        return {"type": "cpu", "metrics": metrics}
