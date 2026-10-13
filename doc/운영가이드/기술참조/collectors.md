# 수집기 상세

각 수집기는 eBPF 프로그램을 커널에 로드하여 이벤트를 수집. GPU 수집기는 예외적으로 별도 라이브러리 사용(미구현) 사용.

## NetworkCollector

**Measurement**: `network_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `packet_count` | int | 패킷 수 (TCP 는 sendmsg/cleanup_rbuf 호출 수) |
| `byte_count` | int | 전송 바이트 수 (ICMP out 은 0) |
| `error_count` | int | 에러 수 (현재 0) |
| `drop_count` | int | 드롭된 패킷 수 (노드 요약 레코드) |
| `retransmit_count` | int | TCP 재전송 수 (out 레코드) |
| `rtt_ms` | float | TCP smoothed RTT — `tcp_sock.srtt_us`, 주기 안 마지막 관측값 |
| `loss_rate` | float | 재전송 / 송신 패킷 (0~1, out 레코드) |
| `inbound_pps` | float | 수신 패킷/초 (노드 요약 레코드) |
| `unique_src_count` | int | 수신 출발지 IP 종류 수 (노드 요약 레코드) |
| `ddos_detected` | bool | `collectors.network.ddos` 임계 초과 여부 (노드 요약 레코드, 규칙 기반) |

**Tags**: `node_id`, `hostname`, `ip`, `protocol`(TCP/UDP/ICMP/ALL), `direction`, `src_ip`, `dst_ip`, `sport`, `dport`

**eBPF 트레이스포인트**:
- `kprobe:tcp_sendmsg` — TCP 송신(out) 패킷/바이트 + RTT
- `kprobe:tcp_cleanup_rbuf` — TCP 수신(in) 패킷/바이트 + RTT
- `tracepoint:tcp:tcp_retransmit_skb` — TCP 재전송 (2026-10)
- `kprobe:udp_sendmsg` / `kprobe:skb_consume_udp` — UDP 송신/수신 (2026-10)
- `kprobe:icmp_rcv` / `kprobe:__icmp_send` — ICMP 수신/송신. IP 헤더에서 주소를 읽고, 송신은 패킷 수만 (2026-10)
- `tracepoint:skb:kfree_skb` — 패킷 드롭 감지

> 연결 4-tuple(src/dst IP·sport·dport) 단위로 집계하고 송·수신을 `direction`(in/out) 태그로 구분. ICMP 는 포트가 없어 `sport`/`dport` 가 빈 값.
> 매 주기 **노드 요약 레코드 1건**(`protocol=ALL`, `direction=in`) 에 드롭 수·수신 pps·출발지 종류·DDoS 판정을 싣는다.
> UDP/ICMP 프로브는 선택 — 커널에 심볼이 없으면 경고만 남기고 TCP 수집은 계속된다 (`network_kprobe_optional_skipped`).

---

## SyscallCollector

**Measurement**: `syscall_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `call_count` | int | 시스템콜 호출 횟수 |
| `total_latency_us` | int | 누적 지연시간 (마이크로초) |
| `avg_latency_us` | float | 평균 지연시간 |
| `min_latency_us` | int | 최소 지연시간 |
| `max_latency_us` | int | 최대 지연시간 |
| `error_count` | int | 에러(음수 반환값) 수 |

**Tags**: `node_id`, `hostname`, `syscall_name`, `syscall_id`, `process_name`

**eBPF 트레이스포인트**:
- `tracepoint:raw_syscalls:sys_enter` — 시스템콜 진입 시간 기록
- `tracepoint:raw_syscalls:sys_exit` — 지연시간 계산 및 에러 감지

> 주요 시스템콜 약 37종에 이름 매핑(`SYSCALL_NAMES`). 매핑에 없는 번호는 `syscall_<id>`로 표기.

---

## CPUCollector

**Measurement**: `cpu_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `user_time_ms` | int | 주기 동안 유저 모드 시간 (user+nice, `/proc/stat` 델타) |
| `system_time_ms` | int | 주기 동안 커널 모드 시간 (system+irq+softirq) |
| `idle_time_ms` | int | 주기 동안 유휴 시간 |
| `iowait_time_ms` | int | 주기 동안 I/O 대기 시간 |
| `usage_percent` | float | 주기 동안 사용률 = (총 − idle − iowait) / 총 |
| `context_switches` | int | 컨텍스트 스위치 수 |
| `irq_count` | int | 하드웨어 인터럽트 수 |
| `softirq_count` | int | SoftIRQ 수 (2026-10) |

**Tags**: `node_id`, `hostname`, `ip`, `cpu_id`

**eBPF 트레이스포인트**:
- `kprobe:finish_task_switch` — 컨텍스트 스위치 추적 (함수형 프로브 `trace_sched_switch` 부착; 커널 빌드에 따라 심볼이 `finish_task_switch.isra.N`)
- `tracepoint:irq:irq_handler_entry` — 하드웨어 인터럽트
- `tracepoint:irq:softirq_entry` — SoftIRQ (2026-10)

> 모드별 CPU 시간은 eBPF 로 직접 셀 수 없어(스케줄러 이벤트는 태스크 단위) `/proc/stat` 의 코어별 누적값을 주기마다 읽어 델타를 낸다 (2026-10, 이전에는 항상 0).
> 첫 주기는 기준만 잡고 시간 필드를 내보내지 않는다.

---

## MemoryCollector

**Measurement**: `memory_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `total_bytes` | int | 전체 메모리 |
| `used_bytes` | int | 사용 중인 메모리 |
| `free_bytes` | int | 여유 메모리 |
| `available_bytes` | int | 사용 가능한 메모리 |
| `usage_percent` | float | 사용률 (%) |
| `kmalloc_count` | int | 커널 메모리 할당 횟수 |
| `kfree_count` | int | 커널 메모리 해제 횟수 |
| `page_faults` | int | 페이지 폴트 수 |
| `oom_count` | int | OOM killer 발동 횟수 |
| `kmalloc_bytes` | int | 커널 할당 바이트 |
| `buffers_bytes` | int | 버퍼 메모리 |
| `cached_bytes` | int | 캐시 메모리 |
| `sin_bytes` | int | Swap-in 바이트 |
| `sout_bytes` | int | Swap-out 바이트 |

**Tags**: `node_id`, `hostname`, `pid`, `memory_type`

**eBPF 트레이스포인트**:
- `kretprobe:kmalloc` — 커널 메모리 할당
- `kprobe:kfree` — 커널 메모리 해제
- `tracepoint:exceptions:page_fault_user` — 유저 스페이스 페이지 폴트
- `tracepoint:oom:mark_victim` — OOM 이벤트
- `tracepoint:kmem:kmem_cache_alloc` — Slab 캐시 할당

---

## FilesystemCollector

**Measurement**: `filesystem_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `io_operations` | int | I/O 작업 수 |
| `bytes_transferred` | int | 전송 바이트 수 |
| `io_latency_us` | float | I/O 평균 지연시간 (마이크로초) |
| `open_count` | int | 파일 열기 횟수 |
| `close_count` | int | 파일 닫기 횟수 |
| `unlink_count` | int | 파일 삭제(unlink/unlinkat) 횟수 (2026-10) |
| `block_read_ops` / `block_write_ops` | int | 블록 레이어 요청 발행 수 — 장치별, `operation=block` 레코드 (2026-10) |
| `block_read_bytes` / `block_write_bytes` | int | 블록 레이어 바이트 |
| `block_latency_us` | float | 블록 요청 발행→완료 평균 지연 |
| `read_count` | int | 읽기 횟수 (psutil disk_io) |
| `write_count` | int | 쓰기 횟수 (psutil disk_io) |
| `read_bytes` / `write_bytes` | int | 디바이스 읽기/쓰기 바이트 (psutil) |
| `read_time_ms` / `write_time_ms` | int | 디바이스 읽기/쓰기 누적 시간 |
| `total_bytes` / `used_bytes` / `free_bytes` / `usage_percent` | - | 마운트별 디스크 사용량 (psutil) |

**Tags**: `node_id`, `hostname`, `ip`, `pid`, `operation`(read/write/block), `mount_point`, `filesystem_type`, `device`

**eBPF 트레이스포인트**:
- `kprobe/kretprobe:vfs_read` — VFS 읽기 및 지연시간
- `kprobe/kretprobe:vfs_write` — VFS 쓰기 및 지연시간
- `tracepoint:syscalls:sys_enter_open` — 파일 열기
- `tracepoint:syscalls:sys_enter_close` — 파일 닫기
- `tracepoint:syscalls:sys_enter_unlink` / `sys_enter_unlinkat` — 파일 삭제 (2026-10)
- `tracepoint:block:block_rq_issue` / `block_rq_complete` — 블록 요청 발행·완료, 장치(dev_t) 단위 집계. 장치명은 `/sys/dev/block/MAJ:MIN` 으로 푼다 (2026-10)

---

## ProcessCollector

**Measurement**: `process_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `fork_count` | int | fork 이벤트 수 |
| `exec_count` | int | exec 이벤트 수 |
| `exit_count` | int | 프로세스 종료 수 |
| `thread_count` | int | 스레드 수 |
| `schedule_count` | int | 스케줄 이벤트 수 |
| `cpu_usage_percent` | float | CPU 사용률 |
| `memory_usage_bytes` | int | 메모리 사용량 |
| `total_count` | int | 전체 프로세스 수 |
| `running_count` | int | 실행 중인 프로세스 수 |
| `sleeping_count` | int | 슬립 중인 프로세스 수 |
| `zombie_count` | int | 좀비 프로세스 수 |

**Tags**: `node_id`, `hostname`, `pid`, `process_name`, `state`

**eBPF 트레이스포인트**:
- `tracepoint:sched:sched_process_fork`
- `tracepoint:sched:sched_process_exec`
- `tracepoint:sched:sched_process_exit`
- `tracepoint:sched:sched_switch`

---

## ContainerCollector

**Measurement**: `container_metrics`

| 필드 | 타입 | 설명 |
|------|------|------|
| `cpu_usage_ns` | int | CPU 사용 시간 (나노초) |
| `memory_usage_bytes` | int | 메모리 사용량 |
| `memory_limit_bytes` | int | 메모리 제한 |
| `memory_usage_percent` | float | 메모리 사용률 (%) |

**Tags**: `node_id`, `hostname`, `container_type`, `container_id`

**데이터 소스** (cgroup v2):
- Docker: `/sys/fs/cgroup/system.slice/docker-*/`
- Kubernetes: `/sys/fs/cgroup/kubepods/pod*/`
- 읽는 파일: `cpu.stat`, `memory.current`, `memory.max`

> eBPF 없이 cgroup 파일 시스템 직접 읽기 방식.

---

## GPUCollector

**Measurement**: `gpu_metrics`

eBPF 미사용. 별도 라이브러리를 통해 수집.
미구현으로 현재는 NVIDIA Management Library(pynvml)의 스펙에 맞추어 정의되어 있음.

| 필드 | 타입 | 설명 |
|------|------|------|
| `gpu_utilization_pct` | float | GPU 코어 사용률 (%) |
| `mem_utilization_pct` | float | GPU 메모리 컨트롤러 사용률 (%) |
| `mem_used_bytes` | int | 사용 중인 VRAM (bytes) |
| `mem_free_bytes` | int | 여유 VRAM (bytes) |
| `mem_total_bytes` | int | 전체 VRAM (bytes) |
| `temperature_celsius` | float | GPU 온도 (°C) |
| `power_watts` | float | 전력 소비량 (W) |
| `fan_speed_pct` | float | 팬 속도 (%) |
| `clock_sm_mhz` | float | SM 클럭 (MHz) |
| `clock_mem_mhz` | float | 메모리 클럭 (MHz) |

**Tags**: `node_id`, `hostname`, `ip`, `gpu_index`, `gpu_name`, `gpu_uuid`

