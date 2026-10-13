# 설정 레퍼런스

설정 파일 위치: `config/config.yaml`

## 전체 예시

```yaml
node_id: "node-001"

influxdb:
  url: "http://192.0.2.226:8086"
  token: "your-token-here"
  org: "nanum"
  bucket: "ebpf_metrics"
  timeout: 30

storage:
  base_path: "./data"
  json_split: "per_cycle"
  json_retention_days: 7
  parquet_retention_days: 30
  compression_level: 9

collection_interval: 10

collectors:
  network:
    enabled: true
    interfaces: ["eth0", "eth1"]
    protocols: ["TCP", "UDP", "ICMP"]

  syscall:
    enabled: true
    tracked_calls: ["read", "write", "open", "close"]

  cpu:
    enabled: true
    per_core: true

  memory:
    enabled: true
    track_oom: true

  filesystem:
    enabled: true
    mount_points: ["/", "/data"]

  process:
    enabled: true
    min_cpu_percent: 1.0

  container:
    enabled: true
    runtime: "docker"

  gpu:
    enabled: false   # GPU 없는 서버 기본값
    collect_interval: 5

grafana:
  url: "http://192.0.2.226:3000"
  datasource: "InfluxDB"

logging:
  level: "INFO"
  format: "json"
  output: "stdout"
```

## 옵션 설명

### 최상위

| 키 | 타입 | 기본값 | 설명 |
|----|------|--------|------|
| `node_id` | string | `"node-001"` | 노드 식별자. `--node-id` CLI 인자로 덮어쓰기 가능 |
| `collection_interval` | int | `10` | 수집 주기 (초) |

### influxdb

| 키 | 설명 |
|----|------|
| `url` | InfluxDB 주소 (마스터 서버 IP 사용) |
| `token` | API 토큰. `scripts/setup_master.sh` 실행 시 자동 설정 |
| `org` | 조직명 |
| `bucket` | 버킷명 |
| `timeout` | 요청 타임아웃 (초) |
| `retry_attempts` | 쓰기 실패 재시도 횟수 (기본 3). 무한 재시도는 두지 않는다 — 큐 적재가 메모리 폭주로 이어진 전례(2026-06) |
| `retry_backoff_sec` | 첫 재시도 대기 (기본 1, 이후 2배씩) |
| `schema_mode` | 스키마 검증: `off` / `warn`(기본) / `strict`. 운영 config 는 `strict` |

### storage

| 키 | 기본값 | 설명 |
|----|--------|------|
| `base_path` | `./data` | 파일 저장 루트 경로 (실제 배포: `/root/eBPF/data`) |
| `json_split` | `per_cycle` | JSONL 분할 방식: `per_cycle`(기존 호환) / `hourly`(연·월·일 폴더, 시간 누적) / `daily`(연·월 폴더, 일 누적) |
| `json_retention_days` | `7` | JSON 파일 보관 기간 (일) |
| `parquet_retention_days` | `30` | Parquet 파일 보관 기간 (일) |
| `compression_level` | `9` | Zstandard 압축 레벨 (1~22) |

### collectors

각 수집기에 공통으로 `enabled: true/false` 옵션이 존재한다.

#### network

| 키 | 설명 |
|----|------|
| `interfaces` | 모니터링할 네트워크 인터페이스 목록 (현재 미사용 — 소켓 단위 집계) |
| `protocols` | 모니터링할 프로토콜 (TCP/UDP/ICMP 모두 수집, 현재 필터로는 미사용) |
| `ddos.inbound_pps_threshold` | 한 주기 수신 패킷/초가 넘으면 `ddos_detected=true` (기본 20000) |
| `ddos.unique_src_threshold` | 한 주기 수신 출발지 IP 종류 수가 넘으면 `ddos_detected=true` (기본 500) |

#### syscall

| 키 | 설명 |
|----|------|
| `tracked_calls` | 추적할 시스템콜 목록 (빈 배열이면 전체 추적) |

#### cpu

| 키 | 설명 |
|----|------|
| `per_core` | `true`이면 코어별 메트릭 수집 |

#### memory

| 키 | 설명 |
|----|------|
| `track_oom` | OOM(Out of Memory) 이벤트 추적 여부 |

#### filesystem

| 키 | 설명 |
|----|------|
| `mount_points` | 모니터링할 마운트 포인트 목록. 지정 시 해당 마운트만 수집, 비우면 squashfs(snap)·tmpfs·proc 등 가상/읽기전용 FS를 자동 제외 |

#### process

| 키 | 설명 |
|----|------|
| `min_cpu_percent` | 해당 값 이상 CPU를 사용하는 프로세스만 수집 |

#### container

| 키 | 설명 |
|----|------|
| `runtime` | 컨테이너 런타임: `docker` 또는 `containerd` |

#### gpu (구현중)

| 키 | 설명 |
|----|------|
| `enabled` | GPU 수집 여부. |
| `collect_interval` | GPU 수집 주기 (초, 기본값 5) |

### logging

| 키 | 값 | 설명 |
|----|-----|------|
| `level` | `DEBUG`, `INFO`, `WARNING`, `ERROR` | 로그 레벨 |
| `format` | `json`, `console` | 출력 형식 |
| `output` | `stdout`, 파일 경로 | 출력 대상 |

## CLI 인자

```
python3 main.py [옵션]

옵션:
  --config PATH     설정 파일 경로 (기본: ./config/config.yaml)
  --node-id ID      노드 ID (config.yaml의 node_id 덮어쓰기)
```
