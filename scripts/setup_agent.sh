#!/bin/bash
# scripts/setup_agent.sh
# 메트릭 에이전트 설치 스크립트 (에이전트 노드 전용)
# 용도: 메트릭 수집기를 실행하는 서버에서 실행
#
# 사용법:
#   sudo bash setup_agent.sh [--influxdb-url URL] [--node-id ID]
#
# 예시:
#   sudo bash setup_agent.sh --influxdb-url http://192.0.2.226:8086 --node-id metric-node-01

set -e

# ─── 인자 파싱 ────────────────────────────────────────────────────────────────
INFLUXDB_URL="http://192.0.2.226:8086"
NODE_ID="node-001"
INSTALL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --influxdb-url) INFLUXDB_URL="$2"; shift 2 ;;
    --node-id)      NODE_ID="$2";      shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================"
echo "  메트릭 수집 에이전트 설치"
echo "  설치 경로  : ${INSTALL_DIR}"
echo "  InfluxDB   : ${INFLUXDB_URL}"
echo "  Node ID    : ${NODE_ID}"
echo "========================================"

# ─── 1. root 권한 확인 ────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  echo "오류: root 권한이 필요합니다. sudo 로 실행하세요."
  exit 1
fi

# ─── 2. 시스템 패키지 설치 ────────────────────────────────────────────────────
echo ""
echo "[1/6] 시스템 패키지 설치..."
apt update -qq
apt install -y \
  linux-headers-$(uname -r) \
  linux-tools-$(uname -r) \
  linux-tools-common \
  bpfcc-tools \
  python3-bpfcc \
  libbpf-dev \
  clang \
  llvm \
  libelf-dev \
  zlib1g-dev \
  build-essential \
  pkg-config \
  bpftrace \
  python3-pip \
  python3-venv \
  curl \
  jq

# ─── 3. NVIDIA 드라이버 / CUDA 확인 ──────────────────────────────────────────
echo ""
echo "[2/6] NVIDIA GPU 환경 확인..."
echo "구현 진행 중인 내용으로 GPU_AVAILABLE=false로 고정."
if command -v nvidia-smi &>/dev/null; then
  echo "  nvidia-smi 감지됨:"
  nvidia-smi --query-gpu=index,name,driver_version --format=csv,noheader \
    | sed 's/^/    GPU /'
  GPU_AVAILABLE=false
else
  echo "  경고: nvidia-smi 를 찾을 수 없습니다."
  echo "  GPU 수집기는 비활성화 상태로 시작됩니다."
  echo "  NVIDIA 드라이버 설치 후 config/config.yaml 에서 gpu.enabled: true 로 변경하세요."
  GPU_AVAILABLE=false
fi

# ─── 4. Python 가상환경 및 패키지 설치 ───────────────────────────────────────
echo ""
echo "[3/6] Python 가상환경 설정..."
cd "${INSTALL_DIR}"

python3 -m venv venv --system-site-packages
source venv/bin/activate

pip install --upgrade pip -q
pip install -r requirements.txt -q

if [ "${GPU_AVAILABLE}" = true ]; then
  echo "  nvidia-ml-py3 설치..."
  pip install nvidia-ml-py3 -q
fi

deactivate

# ─── 5. config.yaml 업데이트 ─────────────────────────────────────────────────
echo ""
echo "[4/6] config.yaml 업데이트..."
CONFIG_FILE="${INSTALL_DIR}/config/config.yaml"

# InfluxDB URL 교체
sed -i "s|url: \"http://.*:8086\"|url: \"${INFLUXDB_URL}\"|" "${CONFIG_FILE}"

# node_id 교체
sed -i "s|node_id: \".*\"|node_id: \"${NODE_ID}\"|" "${CONFIG_FILE}"

# GPU가 없으면 비활성화
if [ "${GPU_AVAILABLE}" = false ]; then
  python3 - "${CONFIG_FILE}" <<'PYEOF'
import re, sys
config_path = sys.argv[1]
with open(config_path) as f:
    content = f.read()
content = re.sub(r'(gpu:\s*\n\s+enabled:)\s*true', r'\1 false', content)
with open(config_path, 'w') as f:
    f.write(content)
PYEOF
fi

echo "  InfluxDB URL : ${INFLUXDB_URL}"
echo "  Node ID      : ${NODE_ID}"
echo "  GPU enabled  : ${GPU_AVAILABLE}"

# ─── 6. InfluxDB 토큰 입력 받기 ───────────────────────────────────────────────
echo ""
echo "[5/6] InfluxDB 연결 설정..."
echo "  DB 서버에서 발급된 InfluxDB API 토큰을 입력하세요."
echo "  (DB 서버에서 'influx auth list' 명령으로 확인 가능)"
echo -n "  토큰 (엔터로 건너뛰기): "
read -r INFLUX_TOKEN

if [ -n "${INFLUX_TOKEN}" ]; then
  sed -i "s|token: \".*\"|token: \"${INFLUX_TOKEN}\"|" "${CONFIG_FILE}"
  echo "  토큰 업데이트 완료"

  # 연결 테스트
  echo "  InfluxDB 연결 테스트..."
  if curl -sf -H "Authorization: Token ${INFLUX_TOKEN}" \
       "${INFLUXDB_URL}/health" | grep -q '"status":"pass"'; then
    echo "  연결 성공"
  else
    echo "  경고: InfluxDB 연결 실패. URL과 토큰을 확인하세요."
  fi
else
  echo "  건너뜀. config/config.yaml 에서 직접 수정하세요."
fi

# ─── 7. systemd 서비스 등록 ───────────────────────────────────────────────────
echo ""
echo "[6/6] systemd 서비스 등록..."
SERVICE_SRC="${INSTALL_DIR}/scripts/ebpf-collector.service"
SERVICE_DEST="/etc/systemd/system/ebpf-collector.service"

# 서비스 파일에 실제 설치 경로 반영
sed "s|WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|g; \
     s|ExecStart=.*|ExecStart=${INSTALL_DIR}/venv/bin/python3 ${INSTALL_DIR}/main.py|g" \
  "${SERVICE_SRC}" > "${SERVICE_DEST}"

systemctl daemon-reload
systemctl enable ebpf-collector

echo ""
echo "========================================"
echo "  에이전트 설치 완료"
echo ""
echo "  서비스 시작:"
echo "    sudo systemctl start ebpf-collector"
echo ""
echo "  로그 확인:"
echo "    sudo journalctl -u ebpf-collector -f"
echo ""
echo "  설정 파일:"
echo "    ${CONFIG_FILE}"
echo "========================================"
