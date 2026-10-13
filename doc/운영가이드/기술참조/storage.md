# 저장소 구조

수집된 메트릭은 **InfluxDB**와 **로컬 파일** 두 곳에 동시 저장.

## InfluxDB

### 연결 설정

```yaml
influxdb:
  url: "http://<마스터서버IP>:8086"
  token: "your-token"
  org: "nanum"
  bucket: "ebpf_metrics"
```

### 데이터 모델

InfluxDB Line Protocol 기준:

```
measurement,tag1=val1,tag2=val2 field1=1i,field2=1.5 timestamp
```

공통 태그 (모든 measurement):

| 태그 | 설명 |
|------|------|
| `node_id` | 노드 식별자 |
| `hostname` | 서버 호스트명 |
| `ip` | 서버 IP |

### 데이터 조회

```bash
# 최근 1분 전체 조회
influx query 'from(bucket:"ebpf_metrics") |> range(start: -1m)'

# 특정 measurement만 조회
influx query '
  from(bucket:"ebpf_metrics")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "cpu_metrics")
'

# 수집기별 최신 데이터 1건
influx query 'from(bucket:"ebpf_metrics") |> range(start: -1h) |> first()'
```

---

## 로컬 파일 (계층 저장)

### 디렉토리 구조

```
data/
├── json/       # 최근 7일 — JSONL 포맷 (분할 방식은 json_split 설정에 따름)
├── parquet/    # 7~30일 — Parquet 컬럼 포맷
└── archive/    # 30일+ — Zstandard 압축 Parquet
```

#### JSONL 분할 방식 (`storage.json_split`)

| 값 | 경로/파일 | 특징 |
|----|----------|------|
| `per_cycle` (기본) | `json/{metric}_{epoch ms}.jsonl` | 수집 주기마다 새 파일. **KETI 기존 연동 호환**. 파일 수 급증 |
| `hourly` | `json/{YYYY}/{MM}/{DD}/{metric}_{정시 ms}.jsonl` | 시간(1h) 단위 누적. 파일 수 급감(권장) |
| `daily` | `json/{YYYY}/{MM}/{metric}_{자정 ms}.jsonl` | 일(1d) 단위 누적. 파일 수 최소 |

> 어느 방식이든 레코드(줄) 스키마는 동일하다. `hourly`/`daily`는 같은 버킷의 모든 주기를 한 파일에 한 줄씩 누적한다.

### 계층 전환 흐름

```
수집 → JSON 저장 (7일)
           ↓ 자동 변환
       Parquet (30일)
           ↓ 자동 압축
       .parquet.zst 아카이브 → 30일 후 자동 삭제
```

백그라운드 스케줄러가 1시간마다 실행하여 계층 전환 및 만료 파일 정리.

**변환 규칙 (2026-10-13 개정 — 보존 결함 수정)**

- JSON → Parquet 변환 단위는 (메트릭, 날짜) 이고, **그 날짜의 마지막 버킷까지 모두 7일을 넘긴 뒤에만** 변환한다.
  이전에는 시간(hour) 파일이 7일 경계를 하나씩 넘을 때마다 그 날짜 parquet 을 덮어써서 마지막 한 시간치만 남았다 (실측 보존율 3.9%).
  따라서 JSONL 은 최장 8일까지 남고, 날짜 parquet 은 하루에 한 번 쓰인다.
- 같은 날짜의 parquet 이 이미 있으면 읽어서 **병합**한 뒤 다시 쓴다. 읽을 수 없는 parquet 은 `*.broken-<epoch>.parquet` 으로 옆에 치운다.
- 쓰기는 `*.parquet.tmp` 에 한 뒤 교체(`os.replace`) 하므로 도중에 죽어도 기존 파일이 깨지지 않는다.
- 로그 `parquet_converted` 에 `rows_added` / `rows_existing` / `rows_total` 이 찍힌다 — 하루치가 몇 행인지 여기서 확인한다.

**InfluxDB 적재 (2026-10-13)**

- `influxdb.schema_mode` — `src/storage/influxdb_schema.py` 의 스키마와 대조. `strict` 면 없는 태그·필드를 떼고 없는 measurement 는 버린다. 타입 불일치는 기록만 한다 (바꾸면 기존 시리즈와 충돌).
- `influxdb.retry_attempts` / `retry_backoff_sec` — 쓰기 실패 시 1·2·4초 백오프로 제한 재시도. 끝까지 실패하면 그 배치만 버리고 수집은 계속된다.

### 파일 형식

| 계층 | 포맷 | 특징 |
|------|------|------|
| JSON | JSONL (줄당 1 객체) | 가독성 높음, 빠른 쓰기 |
| Parquet | Apache Parquet | 컬럼 기반, 쿼리 효율적 |
| Archive | Parquet + Zstandard (레벨 9) | 최고 압축률 |

### 보관 기간 설정

`config/config.yaml`:

```yaml
storage:
  base_path: "./data"         # 파일 저장 루트 (실제 배포: /root/eBPF/data)
  json_split: "per_cycle"     # per_cycle | hourly | daily
  json_retention_days: 7      # JSON 보관 기간
  parquet_retention_days: 30  # Parquet 보관 기간
  compression_level: 9        # 압축 레벨 (1~22)
```
