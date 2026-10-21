# src/collectors/filesystem_collector.py

import time
from bcc import BPF  # 이 줄 추가!
from typing import Dict, Any
from .base_collector import BaseCollector
import structlog

logger = structlog.get_logger()


class FilesystemCollector(BaseCollector):
    """파일시스템 메트릭 수집기"""

    def get_ebpf_program(self) -> str:
        return self.proc_key_define() + """
#include <uapi/linux/ptrace.h>
#include <linux/fs.h>
#include <linux/sched.h>

// I/O 정보 구조체
struct io_info_t {
    u64 timestamp;
    u32 pid;
    char comm[TASK_COMM_LEN];
    char filename[256];
    u64 size;
    u64 latency_ns;
    u8 operation;  // 0: read, 1: write
};

// 파일시스템 통계 구조체
struct fs_stats_t {
    u64 read_count;
    u64 write_count;
    u64 read_bytes;
    u64 write_bytes;
    u64 total_latency_ns;
    u64 open_count;
    u64 close_count;
    u64 unlink_count;      // 2026-10 추가
};

// 집계 키 — 기본은 프로세스 이름(comm). KEY_BY_PID 가 정의되면 pid (2026-10 이전 동작)
// pid 는 재사용되며 계속 바뀌어 하루 59만 시계열을 만들었다 (2026-10)
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

BPF_HASH(fs_stats, struct proc_key_t, struct fs_stats_t);
BPF_HASH(io_start, u64, u64);  // key: pid_tid, value: timestamp
BPF_PERF_OUTPUT(io_events);

// 블록 레이어 — 장치(dev_t) 단위 집계 (2026-10 추가)
struct blk_stats_t {
    u64 read_ops;
    u64 write_ops;
    u64 read_bytes;
    u64 write_bytes;
    u64 completed;
    u64 total_latency_ns;
};
struct blk_req_key_t {
    u32 dev;
    u64 sector;
};
BPF_HASH(blk_stats, u32, struct blk_stats_t);
BPF_HASH(blk_start, struct blk_req_key_t, u64, 10240);   // 발행 시각 (dev, sector) → ns

// VFS read 추적
int trace_vfs_read(struct pt_regs *ctx, struct file *file, 
                   char __user *buf, size_t count) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    struct proc_key_t key = {};
    proc_key(&key);
    u64 ts = bpf_ktime_get_ns();

    io_start.update(&pid_tid, &ts);

    struct fs_stats_t *stats, zero = {};
    stats = fs_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->read_count++;
    }

    return 0;
}

int trace_vfs_read_ret(struct pt_regs *ctx) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    u32 pid = pid_tid >> 32;
    struct proc_key_t key = {};
    proc_key(&key);

    u64 *tsp = io_start.lookup(&pid_tid);
    if (!tsp) {
        return 0;
    }

    u64 latency = bpf_ktime_get_ns() - *tsp;
    ssize_t ret = PT_REGS_RC(ctx);

    if (ret > 0) {
        struct fs_stats_t *stats = fs_stats.lookup(&key);
        if (stats) {
            stats->read_bytes += ret;
            stats->total_latency_ns += latency;
        }

        struct io_info_t info = {};
        info.timestamp = bpf_ktime_get_ns();
        info.pid = pid;
        info.size = ret;
        info.latency_ns = latency;
        info.operation = 0;  // read
        bpf_get_current_comm(&info.comm, sizeof(info.comm));

        io_events.perf_submit(ctx, &info, sizeof(info));
    }

    io_start.delete(&pid_tid);
    return 0;
}

// VFS write 추적
int trace_vfs_write(struct pt_regs *ctx, struct file *file,
                    const char __user *buf, size_t count) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    struct proc_key_t key = {};
    proc_key(&key);
    u64 ts = bpf_ktime_get_ns();

    io_start.update(&pid_tid, &ts);

    struct fs_stats_t *stats, zero = {};
    stats = fs_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->write_count++;
    }

    return 0;
}

int trace_vfs_write_ret(struct pt_regs *ctx) {
    u64 pid_tid = bpf_get_current_pid_tgid();
    u32 pid = pid_tid >> 32;
    struct proc_key_t key = {};
    proc_key(&key);

    u64 *tsp = io_start.lookup(&pid_tid);
    if (!tsp) {
        return 0;
    }

    u64 latency = bpf_ktime_get_ns() - *tsp;
    ssize_t ret = PT_REGS_RC(ctx);

    if (ret > 0) {
        struct fs_stats_t *stats = fs_stats.lookup(&key);
        if (stats) {
            stats->write_bytes += ret;
            stats->total_latency_ns += latency;
        }

        struct io_info_t info = {};
        info.timestamp = bpf_ktime_get_ns();
        info.pid = pid;
        info.size = ret;
        info.latency_ns = latency;
        info.operation = 1;  // write
        bpf_get_current_comm(&info.comm, sizeof(info.comm));

        io_events.perf_submit(ctx, &info, sizeof(info));
    }

    io_start.delete(&pid_tid);
    return 0;
}

// File open 추적
TRACEPOINT_PROBE(syscalls, sys_enter_open) {
    struct proc_key_t key = {};
    proc_key(&key);

    struct fs_stats_t *stats, zero = {};
    stats = fs_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->open_count++;
    }

    return 0;
}

// File close 추적
TRACEPOINT_PROBE(syscalls, sys_enter_close) {
    struct proc_key_t key = {};
    proc_key(&key);

    struct fs_stats_t *stats, zero = {};
    stats = fs_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->close_count++;
    }

    return 0;
}

// File unlink 추적 — unlink(2) 와 unlinkat(2) 둘 다 (glibc 의 unlink 는 unlinkat 으로 간다)
// lookup_or_init 매크로는 실패 시 `return 0` 을 하므로 헬퍼는 int 를 돌려줘야 한다
static inline int count_unlink(void) {
    struct proc_key_t key = {};
    proc_key(&key);
    struct fs_stats_t *stats, zero = {};
    stats = fs_stats.lookup_or_init(&key, &zero);
    if (stats) {
        stats->unlink_count++;
    }
    return 0;
}
TRACEPOINT_PROBE(syscalls, sys_enter_unlink)   { return count_unlink(); }
TRACEPOINT_PROBE(syscalls, sys_enter_unlinkat) { return count_unlink(); }

// 블록 요청 발행 — rwbs[0] 이 'R' 이면 읽기, 'W' 면 쓰기 (F/D/N 등은 바이트 0 이라 건너뜀)
TRACEPOINT_PROBE(block, block_rq_issue) {
    u32 dev = args->dev;
    struct blk_stats_t *st, zero = {};
    st = blk_stats.lookup_or_init(&dev, &zero);
    if (st) {
        char op = args->rwbs[0];
        if (op == 'R') {
            st->read_ops++;
            st->read_bytes += args->bytes;
        } else if (op == 'W') {
            st->write_ops++;
            st->write_bytes += args->bytes;
        }
    }
    struct blk_req_key_t key = {};
    key.dev = dev;
    key.sector = args->sector;
    u64 ts = bpf_ktime_get_ns();
    blk_start.update(&key, &ts);
    return 0;
}

// 블록 요청 완료 — 발행 시각이 있으면 지연 합산
TRACEPOINT_PROBE(block, block_rq_complete) {
    struct blk_req_key_t key = {};
    key.dev = args->dev;
    key.sector = args->sector;
    u64 *tsp = blk_start.lookup(&key);
    if (!tsp) {
        return 0;
    }
    u64 latency = bpf_ktime_get_ns() - *tsp;
    blk_start.delete(&key);

    u32 dev = args->dev;
    struct blk_stats_t *st, zero = {};
    st = blk_stats.lookup_or_init(&dev, &zero);
    if (st) {
        st->completed++;
        st->total_latency_ns += latency;
    }
    return 0;
}
"""

    def load_ebpf(self):
        """eBPF 프로그램 로드 및 함수 연결"""
        try:
            program = self.get_ebpf_program()
            self.bpf = BPF(text=program)

            # kprobe 연결
            self.bpf.attach_kprobe(event="vfs_read", fn_name="trace_vfs_read")
            self.bpf.attach_kretprobe(event="vfs_read", fn_name="trace_vfs_read_ret")
            self.bpf.attach_kprobe(event="vfs_write", fn_name="trace_vfs_write")
            self.bpf.attach_kretprobe(event="vfs_write", fn_name="trace_vfs_write_ret")

            logger.info("filesystem_ebpf_loaded")

        except Exception as e:
            logger.error("filesystem_ebpf_load_failed", error=str(e))
            raise

    _dev_names: Dict[int, str] = {}

    def _dev_name(self, dev: int) -> str:
        """tracepoint 의 dev_t(major<<20 | minor) → 장치 이름 (/sys/dev/block/MAJ:MIN 링크). 못 찾으면 'MAJ:MIN'."""
        name = self._dev_names.get(dev)
        if name:
            return name
        major, minor = dev >> 20, dev & 0xfffff
        name = f"{major}:{minor}"
        try:
            import os
            name = os.path.basename(os.readlink(f"/sys/dev/block/{major}:{minor}"))
        except OSError:
            pass
        self._dev_names[dev] = name
        return name

    async def collect(self) -> Dict[str, Any]:
        """파일시스템 메트릭 수집"""
        import psutil

        metrics = []

        try:
            # eBPF 통계 수집
            if self.bpf is not None:
                stats = self.bpf["fs_stats"]

                for k, v in stats.items():
                    proc = self.proc_tags(k)

                    avg_latency = 0
                    if v.read_count + v.write_count > 0:
                        avg_latency = v.total_latency_ns / (v.read_count + v.write_count)

                    # Read 메트릭
                    if v.read_count > 0:
                        metrics.append({
                            "measurement": "filesystem_metrics",
                            "tags": {
                                **self.get_common_tags(),
                                **proc,
                                "operation": "read"
                            },
                            "fields": {
                                "io_operations": v.read_count,
                                "bytes_transferred": v.read_bytes,
                                "io_latency_us": avg_latency / 1000,
                                "open_count": v.open_count,
                                "close_count": v.close_count,
                                "unlink_count": v.unlink_count
                            },
                            "timestamp": int(time.time() * 1e9)
                        })

                    # Write 메트릭
                    if v.write_count > 0:
                        metrics.append({
                            "measurement": "filesystem_metrics",
                            "tags": {
                                **self.get_common_tags(),
                                **proc,
                                "operation": "write"
                            },
                            "fields": {
                                "io_operations": v.write_count,
                                "bytes_transferred": v.write_bytes,
                                "io_latency_us": avg_latency / 1000,
                                "open_count": v.open_count,
                                "close_count": v.close_count,
                                "unlink_count": v.unlink_count
                            },
                            "timestamp": int(time.time() * 1e9)
                        })

                stats.clear()

                # 블록 레이어 — 장치 단위 (2026-10 추가)
                blk = self.bpf["blk_stats"]
                for k, v in blk.items():
                    if v.read_ops + v.write_ops + v.completed == 0:
                        continue
                    avg_blk_latency = (v.total_latency_ns / v.completed) if v.completed else 0
                    metrics.append({
                        "measurement": "filesystem_metrics",
                        "tags": {
                            **self.get_common_tags(),
                            "device": self._dev_name(k.value),
                            "operation": "block"
                        },
                        "fields": {
                            "block_read_ops": v.read_ops,
                            "block_write_ops": v.write_ops,
                            "block_read_bytes": v.read_bytes,
                            "block_write_bytes": v.write_bytes,
                            "block_latency_us": avg_blk_latency / 1000
                        },
                        "timestamp": int(time.time() * 1e9)
                    })
                blk.clear()

            # 디스크 사용량 정보 (psutil)
            # config의 mount_points가 지정되면 그 마운트만 수집,
            # 비어 있으면 가상/읽기전용 FS(squashfs=snap, tmpfs 등 노이즈)를 자동 제외.
            wanted = set(self.config.get("mount_points") or [])
            excluded_fstypes = {
                "squashfs", "tmpfs", "devtmpfs", "overlay", "ramfs", "autofs",
                "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue",
                "debugfs", "tracefs", "fusectl", "configfs", "binfmt_misc",
                "hugetlbfs", "pstore", "securityfs", "bpf", "nsfs", "efivarfs",
            }
            disk_partitions = psutil.disk_partitions(all=True)
            for partition in disk_partitions:
                if wanted:
                    if partition.mountpoint not in wanted:
                        continue
                elif partition.fstype in excluded_fstypes or partition.fstype.startswith("fuse"):
                    continue
                try:
                    usage = psutil.disk_usage(partition.mountpoint)

                    metrics.append({
                        "measurement": "filesystem_metrics",
                        "tags": {
                            **self.get_common_tags(),
                            "mount_point": partition.mountpoint,
                            "filesystem_type": partition.fstype,
                            "device": partition.device
                        },
                        "fields": {
                            "total_bytes": usage.total,
                            "used_bytes": usage.used,
                            "free_bytes": usage.free,
                            "usage_percent": usage.percent
                        },
                        "timestamp": int(time.time() * 1e9)
                    })
                except PermissionError:
                    continue

            # 디스크 I/O 통계
            disk_io = psutil.disk_io_counters(perdisk=False)
            if disk_io:
                metrics.append({
                    "measurement": "filesystem_metrics",
                    "tags": {
                        **self.get_common_tags(),
                        "device": "all"
                    },
                    "fields": {
                        "read_count": disk_io.read_count,
                        "write_count": disk_io.write_count,
                        "read_bytes": disk_io.read_bytes,
                        "write_bytes": disk_io.write_bytes,
                        "read_time_ms": disk_io.read_time,
                        "write_time_ms": disk_io.write_time
                    },
                    "timestamp": int(time.time() * 1e9)
                })

            logger.debug("filesystem_metrics_collected", count=len(metrics))

        except Exception as e:
            logger.error("filesystem_collect_error", error=str(e))

        return {"type": "filesystem", "metrics": metrics}