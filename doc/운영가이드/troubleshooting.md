# 문제 해결

## ModuleNotFoundError: No module named 'bcc'

**원인**: venv가 시스템 패키지에 접근 불가. venv 생성 시 `--system-site-packages` 필요.

**해결**:
```bash
deactivate
rm -rf venv
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
```

---

## ModuleNotFoundError: No module named 'psutil'

**원인**: `psutil`이 venv에 설치되지 않음 (`requirements.txt`에 포함되어 있으나 설치 중 누락된 경우).

**해결**:
```bash
venv/bin/pip install psutil==5.9.8
sudo systemctl restart ebpf-collector
```

---

## ModuleNotFoundError: No module named 'structlog' 외 기타 모듈 오류

**원인**: venv를 활성화하지 않고 `sudo python3`으로 실행.

**해결**: venv의 python을 절대 경로로 직접 지정.

```bash
# 잘못된 방법
sudo python3 main.py

# 올바른 방법
sudo venv/bin/python3 main.py
```

---

## venv/bin/pip 실행 시 "bad interpreter" 오류

```
/root/.../venv/bin/python3: bad interpreter: No such file or directory
```

**원인**: venv가 다른 경로에서 생성된 후 디렉토리가 이동/복사됨. venv 내부의 pip, python symlink가 원래 경로를 가리키고 있음.

**해결**: venv 재생성.

```bash
rm -rf venv
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

---

## InfluxDB 데이터가 저장되지 않음

**원인**: `config/config.yaml`의 token 오설정.

**확인**:
```bash
# 실제 토큰 조회
influx auth list --host http://<마스터서버IP>:8086

# 에이전트에서 연결 테스트
curl -H "Authorization: Token <토큰>" http://<마스터서버IP>:8086/health
```

**해결**: `config/config.yaml`의 `token` 값을 실제 토큰으로 수정 후 재시작.

```bash
sudo systemctl restart ebpf-collector
```

---

## influx query 결과가 항상 비어있음

**원인**: CLI 기본 설정에 토큰 미설정.

**해결**:
```bash
influx config create \
  --config-name default \
  --host-url http://<마스터서버IP>:8086 \
  --org nanum \
  --token "your-token-here" \
  --active
```

---

## InfluxDB GPG 키 오류 (apt update)

```
NO_PUBKEY DA61C26A0585BD3B
```

**해결**:
```bash
sudo gpg --keyserver keyserver.ubuntu.com --recv-keys DA61C26A0585BD3B
sudo gpg --export DA61C26A0585BD3B | sudo gpg --dearmor >> /etc/apt/trusted.gpg.d/influxdata-archive_compat.gpg
sudo apt update
```

---

## clang warning 과다 출력

```
warning: '__HAVE_BUILTIN_BSWAP32__' macro redefined
```

**원인**: bcc와 커널 헤더 간 매크로 충돌. **기능에는 영향 없음.**
stderr로 출력되므로 `journalctl`(서비스 로그)에는 나타나지 않음.

---

## systemd 서비스 시작 실패

```
Unit ebpf-collector.service not found
```

**원인**: `setup_agent.sh`를 통해 설치하지 않은 경우.

**해결**: [사용 매뉴얼 5-5항](user_manual.md#5-5-systemd-서비스-수동-등록) 참조.

```bash
# 또는 setup_agent.sh 재실행
sudo bash scripts/setup_agent.sh --influxdb-url http://<마스터서버IP>:8086 --node-id <노드이름>
```

---

## InfluxDB 로그인 불가

`scripts/setup_master.sh` 실행 시 자동 생성된 계정:

- **URL**: `http://<마스터서버IP>:8086`
- **Username**: `nanum`
- **Password**: `<INFLUXDB_PASSWORD>`

비밀번호 재설정:
```bash
influx user password --name nanum --host http://localhost:8086
```

토큰 재발급:
```bash
influx auth create \
  --host http://localhost:8086 \
  --org nanum \
  --all-access
```

---

## GPU 수집기가 시작되지 않음 (해당 기능은 현재 구현중으로 nvidia의 nvidia-ml-py3로 대체되어 있는 상태)

**원인 1**: NVIDIA 드라이버 미설치 → `setup_agent.sh`가 자동으로 `gpu.enabled: false` 처리.

**원인 2**: `libnvidia-ml.so.1` 없음 (드라이버 미설치 상태에서 pynvml 실행 시).

```
NVML Shared Library Not Found
```

**해결**: GPU가 없는 서버라면 `config/config.yaml`에서 비활성화.

```yaml
  gpu:
    enabled: false
```

GPU가 있는 서버라면 NVIDIA 드라이버 설치 후:

```bash
source venv/bin/activate
pip install nvidia-ml-py3==7.352.0
deactivate
# config/config.yaml에서 gpu.enabled: true 로 변경
sudo systemctl restart ebpf-collector
```
