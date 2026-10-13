# eBPF 메트릭 수집기 — 사용 매뉴얼

> 대상: 본 시스템을 Ubuntu 22.04 서버에 최초 설치하는 담당자

---

## 목차

1. [시스템 구성 개요](#1-시스템-구성-개요)
2. [사전 요구사항](#2-사전-요구사항)
3. [마스터 서버 설치](#3-마스터-서버-설치)
4. [에이전트 노드 설치](#4-에이전트-노드-설치)
5. [수동 설치 (스크립트 실패 시)](#5-수동-설치-스크립트-실패-시)
6. [수집기 운영](#6-수집기-운영)
7. [데이터 확인](#7-데이터-확인)
8. [설정 변경](#8-설정-변경)
9. [문제 해결](#9-문제-해결)

---

## 1. 시스템 구성 개요

본 시스템은 **마스터 서버**와 **에이전트 노드** 두 종류의 서버로 구성된다.

```
[ 마스터 서버 ]
  InfluxDB 2.x :8086
  Grafana :3000
[ 에이전트 노드 (1개 이상) ]
  eBPF 수집기 (main.py) - 마스터서버 8086 통신(DB)
```

| 서버 종류 | 역할 | 설치 스크립트 |
|-----------|------|--------------|
| 마스터 서버 | 메트릭 저장(InfluxDB) + 시각화(Grafana) | `scripts/setup_master.sh` |
| 에이전트 노드 | 커널 eBPF로 메트릭 수집 후 마스터로 전송 | `scripts/setup_agent.sh` |

> **마스터 서버와 에이전트 노드는 동일 서버에 함께 설치 가능하다.**
> 마스터가 설치된 서버에 에이전트를 함께 설치하면 해당 서버 자체의 메트릭을 수집한다.

---

## 2. 사전 요구사항

### 공통

| 항목 | 조건 |
|------|------|
| OS | Ubuntu 22.04 LTS |
| 권한 | root 또는 sudo 사용 가능 |
| 인터넷 | apt 패키지 설치를 위한 아웃바운드 연결 필요 |

### 마스터 서버

| 항목 | 권장 사양 |
|------|----------|
| CPU | 2코어 이상 |
| RAM | 4GB 이상 (InfluxDB + Grafana) |
| 디스크 | 50GB 이상 (메트릭 데이터 보관 기간에 따라 증가) |
| 포트 개방 | 8086 (InfluxDB), 3000 (Grafana) — 에이전트 노드에서 접근 가능해야 함 |

### 에이전트 노드

| 항목 | 조건 |
|------|------|
| 커널 | 5.0 이상 (`uname -r`로 확인) |
| Python | 3.10 이상 (`python3 --version`으로 확인) |
| 포트 개방 | 아웃바운드 8086 (마스터 서버로 데이터 전송) |

### eBPF 최소 요구사항

| 기능 | 요구 커널 | 비고 |
|------|----------|------|
| `BPF_PERF_OUTPUT` / `TRACEPOINT_PROBE` | 4.7+ | 전체 수집기 공통 |
| `oom:mark_victim` tracepoint | 4.13+ | MemoryCollector |
| `exceptions:page_fault_user` tracepoint | 4.15+ | MemoryCollector |
| cgroup v2 (`memory.current`, `cpu.stat`) | 5.4+ | ContainerCollector — Ubuntu 20.04부터 기본 활성화 |
| bcc 0.18.0 패키지 호환성 | 5.x+ | Ubuntu 22.04 기본 제공 버전 |

기능별 엄밀한 최소 커널은 4.15이나, cgroup v2 기본 활성화 및 bcc 패키지 호환성을 고려하여 **5.0**으로 기재한다.

---

## 3. 마스터 서버 설치

### 3-1. 소스 코드 배포

마스터 서버에 본 프로젝트 디렉토리를 복사한다.

```bash
# scp로 복사하는 경우
scp -r ebpf-metrics-collector/ user@<마스터서버IP>:~/

# git clone을 사용하는 경우
git clone <저장소URL> ebpf-metrics-collector
```

### 3-2. 설치 스크립트 실행

```bash
cd ebpf-metrics-collector
sudo bash scripts/setup_master.sh --bind-addr <마스터서버IP>
```

`<마스터서버IP>`는 에이전트 노드에서 접근 가능한 IP 주소를 지정한다.

```bash
# 예시
sudo bash scripts/setup_master.sh --bind-addr 192.0.2.100
```

스크립트 자동 처리 항목:

1. 기본 패키지 설치 (`curl`, `gnupg`, `jq` 등)
2. InfluxDB 2.x 설치 및 서비스 시작
3. InfluxDB 초기 계정 / 조직 / 버킷 생성
4. Grafana 설치 및 서비스 시작
5. Grafana에 InfluxDB 데이터소스 자동 등록
6. 기본 대시보드(`dashboard.json`) 자동 임포트

### 3-3. 설치 완료 출력 예시

설치 완료 시 아래와 같이 출력된다.

```
========================================
  DB 서버 설치 완료

  InfluxDB
    URL    : http://192.0.2.100:8086
    User   : nanum
    Org    : nanum
    Bucket : ebpf_metrics
    Token  : eyJhb....(긴 토큰)....

  Grafana
    URL      : http://192.0.2.100:3000
    User     : admin
    Password : <INFLUXDB_PASSWORD>

  에이전트 노드 설정 시 아래 명령 사용:
    sudo bash setup_agent.sh \
      --influxdb-url http://192.0.2.100:8086 \
      --node-id <노드이름>
  토큰을 입력하라는 프롬프트에 위 Token 값을 입력하세요.
========================================
```

> **중요**: 출력된 `Token` 값을 반드시 기록한다. 에이전트 노드 설치 시 요구된다.

### 3-4. 초기 계정 정보

| 서비스 | URL | 아이디 | 비밀번호 |
|--------|-----|--------|---------|
| InfluxDB | `http://<마스터서버IP>:8086` | `nanum` | `<INFLUXDB_PASSWORD>` |
| Grafana | `http://<마스터서버IP>:3000` | `admin` | `<INFLUXDB_PASSWORD>` |

### 3-5. 마스터 서버 설치 확인

```bash
# InfluxDB 상태 확인
curl http://localhost:8086/health
# 정상: {"checks":[],"message":"ready for queries and writes","name":"influxdb","status":"pass",...}

# Grafana 상태 확인
curl http://localhost:3000/api/health
# 정상: {"commit":"...","database":"ok","version":"..."}

# 서비스 상태 확인
sudo systemctl status influxdb
sudo systemctl status grafana-server
```

---

## 4. 에이전트 노드 설치

### 4-1. 소스 코드 배포

에이전트 노드에 본 프로젝트 디렉토리를 복사한다.

```bash
scp -r ebpf-metrics-collector/ user@<에이전트IP>:~/
```

### 4-2. 설치 스크립트 실행

```bash
cd ebpf-metrics-collector
sudo bash scripts/setup_agent.sh \
  --influxdb-url http://<마스터서버IP>:8086 \
  --node-id <이노드의이름>
```

**옵션 설명**

| 옵션 | 설명 | 예시 |
|------|------|------|
| `--influxdb-url` | 마스터 서버의 InfluxDB 주소 | `http://192.0.2.100:8086` |
| `--node-id` | 에이전트 노드 식별 이름 | `gpu-server-01`, `worker-node-02` |

```bash
# 실행 예시
sudo bash scripts/setup_agent.sh \
  --influxdb-url http://192.0.2.100:8086 \
  --node-id gpu-server-01
```

스크립트 실행 중 InfluxDB 토큰 입력 프롬프트가 표시된다.

```
[5/6] InfluxDB 연결 설정...
  DB 서버에서 발급된 InfluxDB API 토큰을 입력하세요.
  토큰 (엔터로 건너뛰기): <마스터 서버 설치 시 기록한 토큰 입력>
```

스크립트 자동 처리 항목:

1. 시스템 패키지 설치 (`linux-headers`, `bpfcc-tools`, `clang`, `llvm` 등)
2. NVIDIA GPU 감지 (`nvidia-smi` 미존재 시 GPU 수집기 자동 비활성화)
3. Python 가상환경 생성 및 패키지 설치
4. `config/config.yaml`에 InfluxDB URL, node_id, 토큰 자동 반영
5. systemd 서비스(`ebpf-collector`) 등록 및 자동 시작 설정

### 4-3. venv 재생성 (bcc 오류 발생 시)

`setup_agent.sh` 실행 후 `No module named 'bcc'` 오류가 발생하는 경우, 가상환경이 시스템 패키지를 참조하지 못하는 상태. 아래 명령으로 재생성

```bash
cd ebpf-metrics-collector
rm -rf venv
python3 -m venv venv --system-site-packages   # --system-site-packages 필수
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

> `--system-site-packages` 옵션 없이 생성하면 시스템에 설치된 `bcc`(eBPF Python 바인딩)를 인식하지 못한다.

### 4-4. 서비스 시작

```bash
sudo systemctl start ebpf-collector
sudo systemctl status ebpf-collector
sudo journalctl -u ebpf-collector -f
```

### 4-5. 정상 동작 로그 예시

서비스 시작 직후 아래 로그가 출력되면 정상

```json
{"event": "node_initialized", "node_info": {"node_id": "gpu-server-01", ...}}
{"collector": "network", "event": "collector_initialized"}
{"collector": "cpu",     "event": "collector_initialized"}
...
{"count": 7, "event": "all_collectors_initialized"}
{"event": "metric_collector_starting"}
{"collector": "NetworkCollector", "event": "ebpf_loaded"}
{"collector": "NetworkCollector", "event": "collector_started"}
...
{"collector": "network", "count": 2, "event": "influxdb_batch_written"}
{"collector": "cpu",     "count": 3, "event": "influxdb_batch_written"}
```

`influxdb_batch_written` 로그가 10초 간격으로 반복되면 정상 수집

---

## 5. 수동 설치 (스크립트 실패 시)

자동 설치 스크립트가 실패하거나 특수 환경인 경우 아래 단계를 직접 수행한다.

### 5-1. 시스템 패키지 설치

```bash
sudo apt update
sudo apt install -y \
  linux-headers-$(uname -r) \
  linux-tools-$(uname -r) \
  linux-tools-common \
  bpfcc-tools \
  python3-bpfcc \
  libbpf-dev \
  clang \
  llvm \
  libelf-dev \
  build-essential \
  python3-pip \
  python3-venv \
  curl jq
```

### 5-2. Python 가상환경 생성

```bash
cd ebpf-metrics-collector
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate
```

### 5-3. config.yaml 설정

```bash
vi config/config.yaml
```

필수 수정 항목:

```yaml
node_id: "이-노드의-이름"        # 예: gpu-server-01

influxdb:
  url: "http://<마스터서버IP>:8086"
  token: "<마스터 서버에서 발급된 토큰>"
  org: "nanum"
  bucket: "ebpf_metrics"
```

GPU가 없는 서버의 경우:

```yaml
  gpu:
    enabled: false
```

### 5-4. 동작 테스트 (수동 실행)

```bash
cd ebpf-metrics-collector
sudo venv/bin/python3 main.py --config config/config.yaml
```

> eBPF 프로그램 로드에 root 권한이 필요하다. `source venv/bin/activate` 후 `sudo python3`으로 실행하면 venv가 무시되므로, 위와 같이 venv의 python3 절대 경로를 직접 지정한다.

10초 후 `influxdb_batch_written` 로그가 출력되면 정상이다. `Ctrl+C`로 종료한다.

### 5-5. systemd 서비스 수동 등록

```bash
INSTALL_DIR=$(pwd)   # ebpf-metrics-collector 디렉토리 절대 경로

sudo tee /etc/systemd/system/ebpf-collector.service > /dev/null <<EOF
[Unit]
Description=eBPF Metrics Collector
After=network.target influxdb.service
Wants=influxdb.service

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/main.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ebpf-collector
sudo systemctl start ebpf-collector
```

---

## 6. 수집기 운영

### 서비스 제어

```bash
sudo systemctl start ebpf-collector      # 시작
sudo systemctl stop ebpf-collector       # 중지
sudo systemctl restart ebpf-collector    # 재시작 (설정 변경 후)
sudo systemctl enable ebpf-collector     # 부팅 시 자동 시작 활성화
sudo systemctl disable ebpf-collector    # 부팅 시 자동 시작 비활성화
```

### 로그 확인

```bash
# 실시간 로그
sudo journalctl -u ebpf-collector -f

# 최근 100줄
sudo journalctl -u ebpf-collector -n 100

# 오늘 로그
sudo journalctl -u ebpf-collector --since today

# 특정 시간대 로그
sudo journalctl -u ebpf-collector --since "2025-01-01 09:00:00" --until "2025-01-01 10:00:00"
```

### 수집 상태 빠른 확인

```bash
# 최근 1분 에러 로그 확인
sudo journalctl -u ebpf-collector --since "1 minute ago" | grep '"level":"error"'

# 정상 수집 확인 (10초마다 출력됨)
sudo journalctl -u ebpf-collector --since "1 minute ago" | grep 'influxdb_batch_written'
```

---

## 7. 데이터 확인

### 7-1. InfluxDB CLI (마스터 서버에서 실행)

```bash
# CLI 설정 (최초 1회)
influx config create \
  --config-name default \
  --host-url http://localhost:8086 \
  --org nanum \
  --token "<발급된 토큰>" \
  --active

# 최근 1분 데이터 존재 여부 확인
influx query 'from(bucket:"ebpf_metrics") |> range(start: -1m) |> first()'

# 특정 수집기 데이터 확인 (CPU 예시)
influx query '
  from(bucket:"ebpf_metrics")
  |> range(start: -5m)
  |> filter(fn: (r) => r._measurement == "cpu_metrics")
  |> limit(n: 5)
'

# 수집기별 가장 최근 데이터 1건
influx query 'from(bucket:"ebpf_metrics") |> range(start: -1h) |> last()'
```

수집기별 measurement 이름:

| 수집기 | measurement |
|--------|------------|
| Network | `network_metrics` |
| Syscall | `syscall_metrics` |
| CPU | `cpu_metrics` |
| Memory | `memory_metrics` |
| Filesystem | `filesystem_metrics` |
| Process | `process_metrics` |
| Container | `container_metrics` |
| GPU | `gpu_metrics` |

### 7-2. Grafana 대시보드

브라우저에서 `http://<마스터서버IP>:3000` 접속

- 아이디: `admin`
- 비밀번호: `<INFLUXDB_PASSWORD>`

좌측 메뉴 → Dashboards → 사전 임포트된 eBPF 대시보드 선택

> 데이터가 표시되지 않는 경우 우측 상단 시간 범위를 "Last 15 minutes"으로 변경한다.

### 7-3. 로컬 파일 저장 확인 (에이전트 노드)

수집된 메트릭은 InfluxDB와 함께 에이전트 노드 로컬에도 저장된다.

```bash
ls -la ebpf-metrics-collector/data/

# 구조:
# data/json/      — 최근 7일 (JSONL 형식)
# data/parquet/   — 7~30일 (Parquet 형식)
# data/archive/   — 30일 이상 (압축 아카이브)
```

> `data/json/`의 파일 분할은 `storage.json_split` 설정을 따른다: `per_cycle`(기존 호환, 주기마다 새 파일) / `hourly`(연·월·일 폴더, 시간 누적) / `daily`(연·월 폴더, 일 누적). 자세한 내용은 [저장소 구조](기술참조/storage.md) 참조.

---

## 8. 설정 변경

설정 파일 위치: `config/config.yaml`

설정 변경 후 서비스 재시작이 필요하다.

```bash
sudo systemctl restart ebpf-collector
```

### 수집 간격 변경

```yaml
collection_interval: 10   # 초 단위, 기본값 10
```

### 특정 수집기 비활성화

```yaml
collectors:
  cpu:
    enabled: false
```

### 네트워크 인터페이스 변경

```bash
ip link show   # 현재 인터페이스 목록 확인
```

```yaml
collectors:
  network:
    interfaces: ["eth0", "ens3"]   # 실제 인터페이스 이름으로 변경
```

### 파일시스템 마운트 포인트 변경

```yaml
collectors:
  filesystem:
    mount_points: ["/", "/data", "/mnt/storage"]
```

### GPU 수집기 활성화 (구현 중인 내용. nvidia로 대체함)

```bash
nvidia-smi   # NVIDIA 드라이버 설치 여부 확인

source venv/bin/activate
pip install nvidia-ml-py3==7.352.0
deactivate
```

```yaml
collectors:
  gpu:
    enabled: true
    collect_interval: 5
```

### 로그 레벨 변경

```yaml
logging:
  level: "DEBUG"    # DEBUG / INFO / WARNING / ERROR
```

### 다수의 에이전트 노드 운영

각 에이전트 노드에 고유한 `node_id`를 부여하면 Grafana에서 노드별로 구분하여 확인 가능하다.

```yaml
node_id: "gpu-server-01"   # 노드별로 상이하게 설정
```

---

## 9. 문제 해결

### 수집기 미시작 — bcc 모듈 오류

```
ModuleNotFoundError: No module named 'bcc'
```

**원인**: 가상환경이 시스템 패키지를 참조하지 못하는 상태.

**해결**:

```bash
cd ebpf-metrics-collector
rm -rf venv
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart ebpf-collector
```

---

### InfluxDB에 데이터 미저장

```
{"event": "influxdb_write_failed", "error": "..."}
```

**확인 1**: 토큰 오류 여부

```bash
grep token config/config.yaml
influx auth list --host http://<마스터서버IP>:8086
```

**확인 2**: 네트워크 연결 여부

```bash
curl http://<마스터서버IP>:8086/health
```

**해결**: `config/config.yaml`의 `token` 값을 실제 토큰으로 수정 후 재시작.

```bash
vi config/config.yaml
sudo systemctl restart ebpf-collector
```

---

### InfluxDB 웹 UI 로그인 불가

초기 계정:
- URL: `http://<마스터서버IP>:8086`
- 아이디: `nanum`
- 비밀번호: `<INFLUXDB_PASSWORD>`

비밀번호 분실 시:

```bash
influx user password --name nanum --host http://localhost:8086
```

---

### Grafana 대시보드에 데이터 미표시

**확인 1**: InfluxDB 데이터 존재 여부

```bash
influx query 'from(bucket:"ebpf_metrics") |> range(start: -5m) |> first()' \
  --host http://localhost:8086 \
  --token "<토큰>"
```

**확인 2**: Grafana 데이터소스 연결 상태

Grafana 웹 → Connections → Data sources → InfluxDB → `Test` 버튼 클릭

**확인 3**: 대시보드 우측 상단 시간 범위를 "Last 15 minutes"으로 변경

---

### GPU 수집기 오류

```
error: "NVML Shared Library Not Found"
```

**원인**: NVIDIA 드라이버 미설치 또는 GPU 미탑재 서버.

**해결**: `config/config.yaml`에서 GPU 수집기 비활성화 후 재시작.

```yaml
  gpu:
    enabled: false
```

```bash
sudo systemctl restart ebpf-collector
```

---

### eBPF 로드 시 clang 경고 메시지 출력

```
warning: '__HAVE_BUILTIN_BSWAP32__' macro redefined
```

**원인**: bcc와 커널 헤더 간 매크로 충돌. **기능에는 영향 없음.**
경고는 stderr로 출력되므로 서비스 로그(`journalctl`)에는 표시되지 않는다.

---

### systemd 서비스 미발견

```
Unit ebpf-collector.service not found
```

**해결**: [5-5 수동 등록 절차](#5-5-systemd-서비스-수동-등록) 참조.

---

### 일부 수집기만 시작됨

특정 수집기 로딩 실패 시에도 나머지 수집기는 계속 동작한다.
로그에서 `collector_init_failed` 또는 `collector_start_failed` 이벤트를 확인한다.

```bash
sudo journalctl -u ebpf-collector --since "5 minutes ago" | grep -E 'failed|error'
```

---

### InfluxDB 토큰 분실

```bash
# 기존 토큰 조회
influx auth list --host http://localhost:8086
```

신규 발급이 필요한 경우:

```bash
influx auth create \
  --host http://localhost:8086 \
  --org nanum \
  --all-access
```

발급된 토큰을 에이전트 노드의 `config/config.yaml`에 반영한다.

---

## 부록 — 설치 확인 체크리스트

### 마스터 서버

- [ ] `curl http://localhost:8086/health` → `"status":"pass"` 확인
- [ ] `curl http://localhost:3000/api/health` → `"database":"ok"` 확인
- [ ] `http://<마스터서버IP>:8086` 브라우저 접속 → 로그인 성공
- [ ] `http://<마스터서버IP>:3000` 브라우저 접속 → 로그인 성공
- [ ] InfluxDB 토큰 기록 완료

### 에이전트 노드

- [ ] `sudo systemctl status ebpf-collector` → `active (running)` 확인
- [ ] `sudo journalctl -u ebpf-collector -n 20` → `influxdb_batch_written` 로그 확인
- [ ] InfluxDB에 데이터 수신 확인 (마스터 서버에서 query 실행)
- [ ] Grafana 대시보드에서 데이터 시각화 확인
