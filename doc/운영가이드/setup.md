# 설치 가이드

## 구성 개요

본 시스템은 **마스터 서버**와 **에이전트 노드**로 분리 구성.

| 역할 | 설치 스크립트 | 주요 구성 요소 |
|------|--------------|--------------|
| 마스터 서버 | `scripts/setup_master.sh` | InfluxDB 2.x, Grafana |
| 에이전트 노드 | `scripts/setup_agent.sh` | 메트릭 수집기 |

처음 설치하는 경우 [사용 매뉴얼](user_manual.md)을 먼저 참고.

---

## 1. 마스터 서버 설치

Ubuntu 22.04 LTS 기준.

```bash
sudo bash scripts/setup_master.sh --bind-addr <마스터서버IP>
```

자동 처리 항목:

1. InfluxData GPG 키 등록 및 `influxdb2`, `influxdb2-cli` 설치
2. 계정(`nanum`) / 조직(`nanum`) / 버킷(`ebpf_metrics`) 생성
3. Grafana GPG 키 등록 및 설치
4. InfluxDB 데이터소스 자동 등록
5. 기본 대시보드(`dashboard.json`) 임포트

완료 후 출력되는 **InfluxDB API 토큰**을 기록. 에이전트 노드 설치 시 필요.

웹 UI:
- InfluxDB: `http://<마스터서버IP>:8086` (nanum / <INFLUXDB_PASSWORD>)
- Grafana: `http://<마스터서버IP>:3000` (admin / <INFLUXDB_PASSWORD>)

---

## 2. 에이전트 노드 설치

Ubuntu 22.04 LTS (Linux 커널 5.0+) 기준. root 권한 필요.

```bash
sudo bash scripts/setup_agent.sh \
  --influxdb-url http://<마스터서버IP>:8086 \
  --node-id <노드이름>
```

자동 처리 항목:

1. eBPF 관련 시스템 패키지 설치 (`linux-headers`, `clang`, `llvm`, `bpfcc-tools` 등)
2. NVIDIA GPU 환경 감지 — `nvidia-smi` 없으면 `gpu.enabled: false` 자동 설정
3. Python 가상환경 생성 및 `requirements.txt` 설치
4. `config/config.yaml`에 InfluxDB URL, node_id, 토큰 자동 반영
5. systemd 서비스(`ebpf-collector`) 등록

### Python 가상환경 — bcc 오류 발생 시

설치 후 `No module named 'bcc'` 오류가 발생하면 아래와 같이 재생성:

```bash
rm -rf venv
python3 -m venv venv --system-site-packages   # --system-site-packages 필수
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

> `--system-site-packages` 없이 생성하면 시스템에 설치된 `bcc` 모듈을 찾지 못함.

---

## 3. 수집기 시작

```bash
# systemd 서비스로 시작 (권장)
sudo systemctl start ebpf-collector
sudo systemctl status ebpf-collector
sudo journalctl -u ebpf-collector -f
```

---

## 4. 설정 파일 확인

```bash
cat config/config.yaml
```

주요 항목:

```yaml
node_id: "<노드이름>"

influxdb:
  url: "http://<마스터서버IP>:8086"
  token: "<API 토큰>"
  org: "nanum"
  bucket: "ebpf_metrics"

collectors:
  gpu:
    enabled: false   # GPU 없는 서버는 false
```

전체 옵션은 [설정 레퍼런스](기술참조/config.md) 참조.

---

## 5. 데이터 수집 확인

```bash
# CLI 기본 설정 (최초 1회)
influx config create \
  --config-name default \
  --host-url http://<마스터서버IP>:8086 \
  --org nanum \
  --token "<API 토큰>" \
  --active

# 최근 1분 데이터 조회
influx query 'from(bucket:"ebpf_metrics") |> range(start: -1m) |> first()'
```

Grafana에서는 `http://<마스터서버IP>:3000` → 사전 임포트된 대시보드를 통해 확인.
