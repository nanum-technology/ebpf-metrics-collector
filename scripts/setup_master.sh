#!/bin/bash
# scripts/setup_master.sh
# 마스터 서버 설치 스크립트 (InfluxDB + Grafana 통합)
# 용도: 메트릭을 수집·저장·시각화하는 전용 마스터 서버에서 실행
#       에이전트 노드와 분리된 별도 서버에서 한 번만 실행
#
# 사용법:
#   sudo bash setup_master.sh [--bind-addr IP]
#
# 예시:
#   sudo bash setup_master.sh --bind-addr 192.0.2.226

set -e

# ─── 설정 변수 ────────────────────────────────────────────────────────────────
BIND_ADDR="0.0.0.0"
INFLUX_USERNAME="nanum"
INFLUX_PASSWORD="<INFLUXDB_PASSWORD>"
INFLUX_ORG="nanum"
INFLUX_BUCKET="ebpf_metrics"
INFLUX_HOST="http://localhost:8086"
GRAFANA_HOST="http://localhost:3000"
GRAFANA_USER="admin"
GRAFANA_PASSWORD="<INFLUXDB_PASSWORD>"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bind-addr) BIND_ADDR="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "========================================"
echo "  DB 서버 설치 (InfluxDB + Grafana)"
echo "  바인드 주소 : ${BIND_ADDR}"
echo "========================================"

# ─── 1. root 권한 확인 ────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  echo "오류: root 권한이 필요합니다. sudo 로 실행하세요."
  exit 1
fi

# ─── 2. 기본 패키지 설치 ──────────────────────────────────────────────────────
echo ""
echo "[1/7] 기본 패키지 설치..."
apt update -qq
apt install -y curl gnupg apt-transport-https software-properties-common python3 jq

# ─── 3. InfluxDB 설치 ─────────────────────────────────────────────────────────
echo ""
echo "[2/7] InfluxDB 리포지토리 등록..."

curl -fsSL https://repos.influxdata.com/influxdata-archive_compat.key \
  | gpg --dearmor \
  | tee /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg > /dev/null

gpg --keyserver keyserver.ubuntu.com --recv-keys DA61C26A0585BD3B 2>/dev/null || true
gpg --export DA61C26A0585BD3B \
  | gpg --dearmor >> /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg

echo 'deb [signed-by=/etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg] https://repos.influxdata.com/debian stable main' \
  | tee /etc/apt/sources.list.d/influxdata.list

echo "[3/7] InfluxDB 설치..."
apt update -qq
apt install -y influxdb2 influxdb2-cli

# 외부 바인드 주소 설정 (에이전트 노드에서 접근 가능하도록)
if [ "${BIND_ADDR}" != "127.0.0.1" ]; then
  INFLUX_CONF="/etc/influxdb/config.toml"
  if [ -f "${INFLUX_CONF}" ]; then
    sed -i "s|# bind-address = .*|bind-address = \"${BIND_ADDR}:8086\"|" "${INFLUX_CONF}" || true
  fi
fi

systemctl enable influxdb
systemctl start influxdb

echo "InfluxDB 준비 대기 중..."
for i in $(seq 1 20); do
  if curl -sf "${INFLUX_HOST}/health" | grep -q '"status":"pass"'; then
    echo "  InfluxDB 준비 완료"
    break
  fi
  sleep 2
done

# ─── 4. InfluxDB 초기 설정 ────────────────────────────────────────────────────
echo ""
echo "[4/7] InfluxDB 초기 설정..."

# 이미 설정된 경우 건너뜀
if influx org list --host "${INFLUX_HOST}" 2>/dev/null | grep -q "${INFLUX_ORG}"; then
  echo "  이미 초기화 되어 있습니다. 건너뜁니다."
else
  influx setup \
    --host "${INFLUX_HOST}" \
    --username "${INFLUX_USERNAME}" \
    --password "${INFLUX_PASSWORD}" \
    --org "${INFLUX_ORG}" \
    --bucket "${INFLUX_BUCKET}" \
    --retention 0 \
    --force
fi

# 토큰 추출
INFLUX_TOKEN=$(influx auth list \
  --host "${INFLUX_HOST}" \
  --hide-headers \
  | head -1 \
  | awk -F'\t' '{print $3}')

echo "  InfluxDB 설정 완료"
echo "  Token: ${INFLUX_TOKEN}"

# ─── 5. Grafana 설치 ──────────────────────────────────────────────────────────
echo ""
echo "[5/7] Grafana 리포지토리 등록..."

curl -fsSL https://packages.grafana.com/gpg.key \
  | gpg --dearmor -o /etc/apt/trusted.gpg.d/grafana.gpg

echo "deb [signed-by=/etc/apt/trusted.gpg.d/grafana.gpg] https://packages.grafana.com/oss/deb stable main" \
  | tee /etc/apt/sources.list.d/grafana.list

echo "[6/7] Grafana 설치..."
apt update -qq
apt install -y grafana

systemctl enable grafana-server
systemctl start grafana-server

echo "Grafana 준비 대기 중..."
for i in $(seq 1 15); do
  if curl -sf "${GRAFANA_HOST}/api/health" | grep -q '"database": "ok"'; then
    echo "  Grafana 준비 완료"
    break
  fi
  sleep 2
done

grafana-cli admin reset-admin-password "${GRAFANA_PASSWORD}"

# ─── 6. Grafana 데이터소스 및 대시보드 설정 ───────────────────────────────────
echo ""
echo "[7/7] Grafana 데이터소스 및 대시보드 등록..."

# InfluxDB 데이터소스 등록
DS_RESULT=$(curl -sf -X POST "${GRAFANA_HOST}/api/datasources" \
  -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"InfluxDB\",
    \"type\": \"influxdb\",
    \"url\": \"${INFLUX_HOST}\",
    \"access\": \"proxy\",
    \"jsonData\": {
      \"version\": \"Flux\",
      \"organization\": \"${INFLUX_ORG}\",
      \"defaultBucket\": \"${INFLUX_BUCKET}\",
      \"tlsSkipVerify\": true
    },
    \"secureJsonData\": {
      \"token\": \"${INFLUX_TOKEN}\"
    }
  }")

echo "  데이터소스 등록: $(echo "${DS_RESULT}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message', d.get('name', 'ok')))" 2>/dev/null || echo 'done')"

# 대시보드 임포트
DASHBOARD_FILE="${SCRIPT_DIR}/dashboard.json"
if [ -f "${DASHBOARD_FILE}" ]; then
  DS_UID=$(curl -s "${GRAFANA_HOST}/api/datasources/name/InfluxDB" \
    -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['uid'])")

  DASHBOARD=$(sed "s/\${DS_INFLUXDB}/${DS_UID}/g" "${DASHBOARD_FILE}")

  curl -s -X POST "${GRAFANA_HOST}/api/dashboards/import" \
    -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "{\"dashboard\": ${DASHBOARD}, \"overwrite\": true, \"folderId\": 0}" \
    | python3 -m json.tool 2>/dev/null | grep -E '"status"|"slug"' || true

  echo "  대시보드 임포트 완료"
else
  echo "  경고: dashboard.json 파일을 찾을 수 없습니다."
fi

# ─── 완료 출력 ────────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "========================================"
echo "  DB 서버 설치 완료"
echo ""
echo "  InfluxDB"
echo "    URL    : http://${SERVER_IP}:8086"
echo "    User   : ${INFLUX_USERNAME}"
echo "    Org    : ${INFLUX_ORG}"
echo "    Bucket : ${INFLUX_BUCKET}"
echo "    Token  : ${INFLUX_TOKEN}"
echo ""
echo "  Grafana"
echo "    URL      : http://${SERVER_IP}:3000"
echo "    User     : ${GRAFANA_USER}"
echo "    Password : ${GRAFANA_PASSWORD}"
echo ""
echo "  에이전트 노드 설정 시 아래 명령 사용:"
echo "    sudo bash setup_agent.sh \\"
echo "      --influxdb-url http://${SERVER_IP}:8086 \\"
echo "      --node-id <노드이름>"
echo "  토큰을 입력하라는 프롬프트에 위 Token 값을 입력하세요."
echo "========================================"
