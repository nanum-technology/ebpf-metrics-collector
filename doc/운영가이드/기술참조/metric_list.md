# eBPF 메트릭 수집 목록

## 1. 네트워크 (Network)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| 패킷 수집량 / 전송량 | 구현 | `src/collectors/network_collector.py` (line 26-39) | `packet_count`, `byte_count` |
| TCP 연결 상태 탐지 | 구현 | `src/collectors/network_collector.py` (line 31-36) | TCP 송신 추적 |
| RTT (Round Trip Time) | 구현 | `src/collectors/network_collector.py` | `tcp_sock.srtt_us` → `rtt_ms` (2026-10) |
| 패킷 손실률 | 구현 | `src/collectors/network_collector.py` | `loss_rate` = 재전송/송신 패킷 (2026-10) |
| 재전송 횟수 | 구현 | `src/collectors/network_collector.py` | `tracepoint:tcp:tcp_retransmit_skb` → `retransmit_count` (2026-10) |
| DDoS 트래픽 탐지 | 구현 | `src/collectors/network_collector.py` | 규칙 기반 — 수신 pps·출발지 종류 임계(`collectors.network.ddos`), 노드 요약 레코드 `ddos_detected` (2026-10) |
| 프로토콜별 트래픽 (TCP, UDP, ICMP) | 구현 | `src/collectors/network_collector.py` | UDP(`udp_sendmsg`/`skb_consume_udp`) · ICMP(`icmp_rcv`/`__icmp_send`) 추가 (2026-10) |
| 포트별 트래픽 | 구현 | `src/collectors/network_collector.py` | `sport`/`dport`/`src_ip`/`dst_ip` 태그 노출 (KETI 260529 문의 반영). 32768 이상 임시 포트는 `eph` 로 묶음 (2026-10-13) |
| 수신(inbound) 트래픽 | 구현 | `src/collectors/network_collector.py` | `kprobe:tcp_cleanup_rbuf`, `direction=in/out` (2026-06 추가) |
| 패킷 드롭 원인 분석 | 부분 구현 | `src/collectors/network_collector.py` | `kfree_skb` 추적만 |

---

## 2. 시스템 콜 (System Call)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| 시스템 콜 빈도 (read, write, open, close) | 구현 | `src/collectors/syscall_collector.py` (line 18-74) | 전체 syscall 추적 |
| 시스템 콜 지연 시간 | 구현 | `src/collectors/syscall_collector.py` (line 53-58) | min/max/avg 포함 |
| 프로세스별 시스템 콜 사용량 | 구현 | `src/collectors/syscall_collector.py` (line 19-20) | pid_tid 기반 추적 |
| 에러 발생률 | 구현 | `src/collectors/syscall_collector.py` (line 62-65) | 음수 반환값 검사 |

---

## 3. CPU

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| CPU 사용량 (user/system time) | 구현 | `src/collectors/cpu_collector.py` | `/proc/stat` 코어별 델타 — user/system/idle/iowait ms + `usage_percent` (2026-10) |
| 커널 함수 호출 시간 | 부분 구현 | `src/collectors/cpu_collector.py` | 구조체만 정의 |
| CPU 주파수 상태 변화 | 미구현 | — | cpufreq 추적 필요 |
| IRQ 사용량 | 구현 | `src/collectors/cpu_collector.py` | `tracepoint:irq:irq_handler_entry` + `irq:softirq_entry` → `softirq_count` (2026-10) |
| 컨텍스트 스위칭 빈도 | 구현 | `src/collectors/cpu_collector.py` | `kprobe:finish_task_switch` 부착 (2026-06 수정) |

> CPU 주파수 상태 변화: eBPF로 직접 추적 불가. sysfs 파일 읽기 방식 필요.

---

## 4. 메모리 (Memory)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| 메모리 할당/해제 이벤트 추적 (kmalloc, kfree) | 구현 | `src/collectors/memory_collector.py` (line 31-92) | kmalloc/kfree 추적 |
| 페이지 폴트 이벤트 | 구현 | `src/collectors/memory_collector.py` (line 94-104) | page_fault_user |
| 메모리 누수 탐지 | 부분 구현 | `src/collectors/memory_collector.py` (line 36-38) | alloc 추적, 분석 로직 미구현 |
| 슬랩 캐시 사용량 | 부분 구현 | `src/collectors/memory_collector.py` (line 106-117) | kmem_cache_alloc 추적 |

---

## 5. 파일시스템/스토리지 (Filesystem/Storage)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| I/O 요청량 및 대기시간 | 구현 | `src/collectors/filesystem_collector.py` (line 36-113) | vfs_read/write 추적 |
| 읽기/쓰기 속도 | 구현 | `src/collectors/filesystem_collector.py` (line 64-65) | bytes + latency |
| 파일 오픈/닫기/삭제 이벤트 | 구현 | `src/collectors/filesystem_collector.py` | open/close + unlink/unlinkat → `unlink_count` (2026-10) |
| 디스크 블록 사용량 | 구현 | `src/collectors/filesystem_collector.py` | `tracepoint:block:block_rq_issue/complete` — 장치별 ops·bytes·지연, `operation=block` (2026-10) |

---

## 6. 프로세스/스레드 (Process/Thread)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| 프로세스 생성/종료 이벤트 | 구현 | `src/collectors/process_collector.py` (line 29-91) | fork/exec/exit |
| 프로세스/스레드 상태 변화 (sleep, wakeup) | 부분 구현 | `src/collectors/process_collector.py` (line 93-110) | sched_switch만 |
| 스케줄링 지연 시간 | 부분 구현 | `src/collectors/process_collector.py` (line 15) | 측정 로직 미구현 |
| fork/exec 이벤트 | 구현 | `src/collectors/process_collector.py` (line 29-76) | 완전 구현 |
| 프로세스 간 IPC 사용량 | 미구현 | — | IPC 시스템 콜 추적 필요 |

---

## 7. 컨테이너 / Kubernetes

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| Pod / Container별 리소스 사용량 | 부분 구현 | `src/collectors/container_collector.py` | cgroup 기반 (memory.current 등) |
| Namespace별 네트워크 사용량 | 미구현 | — | netns 추적 필요 |
| cgroup CPU 사용량 (`cpu_usage_ns`) | 구현 | `src/collectors/container_collector.py` | `cpu.stat`의 `usage_usec` 파싱 (2026-06 수정, 이전 항상 0) |
| cgroup I/O / quota 제한 | 미구현 | — | `cpu.max`/`io.stat` 파싱 필요 |
| 스토리지 (PV, PVC) | 미구현 | — | Kubernetes API 필요 |

> PV/PVC: eBPF로 직접 수집 불가. Kubernetes API 연동 필요.

---

## 8. GPU (미구현)

| 메트릭 항목 | 구현 상태 | 코드 위치 | 비고 |
|------------|---------|----------|------|
| GPU 코어 사용률 | 구현 | `src/collectors/gpu_collector.py` |  -  |
| GPU 메모리 사용/여유/전체 | 구현 | `src/collectors/gpu_collector.py` | VRAM 정보 |
| GPU 온도 | 구현 | `src/collectors/gpu_collector.py` | °C 단위 |
| 전력 소비량 | 구현 | `src/collectors/gpu_collector.py` | 와트 단위 |
| 팬 속도 | 구현 | `src/collectors/gpu_collector.py` | % 단위 |
| SM / 메모리 클럭 | 구현 | `src/collectors/gpu_collector.py` | MHz 단위 |
| 다중 GPU 지원 | 구현 | `src/collectors/gpu_collector.py` | gpu_index 태그로 구분 |

---

## 전체 구현 통계

### 구현 상태별

| 상태 | 개수 | 비율 |
|------|------|------|
| 완전 구현 | 33개 | 72% |
| 부분 구현 | 8개 | 17% |
| 미구현 | 5개 | 11% |

> 2026-10-13 갱신 — 네트워크 5(RTT·손실률·재전송·DDoS·UDP/ICMP) · CPU 2(user/system 시간·SoftIRQ) · 파일시스템 2(unlink·블록 I/O) 가 구현으로 올라갔다.
> 남은 부분 구현: 커널 함수 호출 시간 · 메모리 누수 분석 · 슬랩 캐시 · 프로세스 상태 변화 · 스케줄링 지연 · Pod 리소스 · 패킷 드롭 원인 · TCP 연결 상태.
> 남은 미구현: CPU 주파수(sysfs) · IPC 사용량 · Namespace 네트워크 · cgroup I/O quota · K8s PV/PVC(API).

### 카테고리별

| 카테고리 | 구현률 | 상태 |
|---------|-------|------|
| 네트워크 | 90% | 양호 |
| 시스템 콜 | 100% | 완료 |
| CPU | 85% | 양호 |
| 메모리 | 75% | 양호 |
| 파일시스템 | 100% | 완료 |
| 프로세스 | 80% | 양호 |
| 컨테이너 | 50% | 보통 |
| GPU | 100% (pynvml, 기본 비활성) | 완료 |

> 달성도는 2026-10-13 기준 (이전 2026-06-04 갱신본은 네트워크 65% · CPU 75% · 파일시스템 60%).

---

## eBPF로 수집 불가능한 메트릭

| 메트릭 | 이유 | 대안 |
|--------|------|------|
| CPU 주파수 상태 | eBPF는 커널 이벤트만 추적 | `/sys/devices/system/cpu/cpu*/cpufreq/` 읽기 |
| Kubernetes PV/PVC | 커널 외부 정보 | Kubernetes API 사용 |

---

## 추가 구현 권장사항

### 높은 우선순위

1. 메모리 누수 분석 로직 — alloc/free 불일치 탐지
2. 스케줄링 지연 시간 — sched_wakeup → sched_switch 간격
3. 수집 상태 메타 — 프로브 부착·맵 읽기 성공 여부를 레코드에 기록 (미수집과 0 의 구분)

### 중간 우선순위

4. IPC 사용량 추적
5. Namespace별 네트워크 격리
6. cgroup I/O / quota (`io.stat`, `cpu.max`)

### 낮은 우선순위

7. CPU 주파수 추적 (sysfs)
8. K8s PV/PVC (API 연동)

> 2026-10-13 완료: DDoS 탐지 · UDP/ICMP · 블록 I/O · RTT · unlink · 재전송/손실률 · CPU 시간 · SoftIRQ.
