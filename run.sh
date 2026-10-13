#!/usr/bin/env bash
#
# eBPF Metrics Collector 실행 스크립트 (에이전트 노드용)
#
# 사용법:
#   sudo ./run.sh                       # 기본 설정으로 실행
#   sudo ./run.sh --node-id node-001    # 노드 ID 지정 (main.py 인자 그대로 전달)
#   sudo ./run.sh --config ./config/config.yaml
#
# 최초 설치는 아래 setup 스크립트를 먼저 수행하세요.
#   - 마스터 서버(InfluxDB+Grafana): sudo bash scripts/setup_master.sh --bind-addr <마스터IP>
#   - 에이전트 노드(수집기):          sudo bash scripts/setup_agent.sh --influxdb-url http://<마스터IP>:8086 --node-id <이름>
#   설치 후 config/config.yaml 의 influxdb url/token/org 가 올바른지 확인하세요.
#
# 백그라운드 실행:  sudo nohup ./run.sh > collector.log 2>&1 &
#
# systemd 서비스 등록 (권장 — 부팅 시 자동 시작, 비정상 종료 시 재시작):
#   sudo cp scripts/ebpf-collector.service /etc/systemd/system/
#   sudo systemctl daemon-reload
#   sudo systemctl enable ebpf-collector      # 부팅 자동 시작 등록
#   sudo systemctl start  ebpf-collector      # 즉시 시작
#   sudo systemctl status ebpf-collector      # 상태 확인
#   sudo journalctl -u ebpf-collector -f      # 로그 실시간
#   (scripts/setup_agent.sh 로 설치하면 위 등록까지 자동 수행됨)

set -euo pipefail

# 스크립트 위치로 이동 (어디서 호출해도 동작)
cd "$(dirname "$0")"

# root 권한 확인 (eBPF 프로그램 로드에 필요)
if [[ "${EUID}" -ne 0 ]]; then
    echo "Error: eBPF 프로그램 로드를 위해 root 권한이 필요합니다. sudo 로 실행하세요." >&2
    exit 1
fi

# 가상환경 활성화 (없으면 시스템 python 사용)
if [[ -f venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

# 수집기 실행 (전달된 인자는 main.py 로 그대로 패스스루)
# config 기본값은 main.py 의 ./config/config.yaml 이며, --config 로 변경 가능
exec python3 main.py "$@"
