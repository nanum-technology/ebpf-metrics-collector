# eBPF Metrics Collector

[![CI](https://github.com/nanum-technology/ebpf-metrics-collector/actions/workflows/ci.yml/badge.svg)](https://github.com/nanum-technology/ebpf-metrics-collector/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Linux 커널 eBPF 로 스토리지 노드의 핵심 자원 메트릭을 수집해 InfluxDB 와 로컬 계층 저장소(JSONL → Parquet → Zstd)에
적재하는 모니터링 에이전트입니다. 애플리케이션이나 커널을 고치지 않고 tracepoint / kprobe 에 프로그램을 붙여
네트워크 · 시스템 콜 · CPU · 메모리 · 파일시스템 · 프로세스 · 컨테이너 7개 영역을 10초 주기로 모읍니다 (GPU 는 NVML).

An eBPF-based, agentless metrics collector for storage nodes. It attaches to kernel tracepoints/kprobes, aggregates
in kernel maps, snapshots them every 10 seconds, and writes to InfluxDB 2.x plus a tiered local store.

## Features

- **Low overhead** — per-event data never leaves the kernel; collectors read aggregated maps once per cycle
- **7 eBPF collectors + GPU** — network (TCP/UDP/ICMP, 4-tuple, RTT, retransmits), syscalls, CPU, memory, filesystem (VFS + block layer), processes, containers (cgroup v2)
- **Structured output** — one schema (`src/storage/influxdb_schema.py`) for all measurements, enforced at write time (`schema_mode: off|warn|strict`)
- **Tiered local storage** — JSONL (7 days) → daily Parquet (30 days) → Zstandard archive, rotated automatically
- **Resilient writes** — synchronous InfluxDB writes with bounded retry; one failing collector never stops the others
- **Multi-node ready** — every record carries `node_id` / `hostname` / `ip`

## Requirements

| | |
|---|---|
| OS | Ubuntu 22.04 LTS (kernel 5.x / 6.x), root |
| Python | 3.10+ |
| eBPF | [bcc](https://github.com/iovisor/bcc) (`python3-bpfcc`) |
| Storage | InfluxDB 2.x (any host), optional Grafana |

## Quick start

```bash
# 1) master (InfluxDB + Grafana) — once
sudo bash scripts/setup_master.sh --bind-addr <MASTER_IP>

# 2) each agent node
sudo bash scripts/setup_agent.sh --influxdb-url http://<MASTER_IP>:8086 --node-id <NODE_ID>
sudo systemctl start ebpf-collector
sudo journalctl -u ebpf-collector -f
```

`--node-id` must be unique per node — identical ids collapse series in InfluxDB.
Manual run: `sudo ./run.sh --node-id <NODE_ID>`.

Before running `scripts/setup_master.sh`, set `INFLUX_PASSWORD` and `GRAFANA_PASSWORD` at the top of the script
(they ship as `<INFLUXDB_PASSWORD>` placeholders). The agent setup asks for the InfluxDB token interactively.

## Configuration

`config/config.yaml` — see [doc/운영가이드/기술참조/config.md](doc/운영가이드/기술참조/config.md).

| key | purpose |
|---|---|
| `collectors.<name>.enabled` | turn a collector on/off |
| `collection_interval` | seconds between snapshots (default 10) |
| `influxdb.schema_mode` | `off` / `warn` / `strict` schema enforcement |
| `influxdb.retry_attempts`, `retry_backoff_sec` | bounded write retry |
| `storage.json_split` | `per_cycle` / `hourly` / `daily` JSONL layout |
| `collectors.network.ddos` | rule-based inbound pps / unique-source thresholds |

## Documentation

| doc | |
|---|---|
| [User manual](doc/운영가이드/user_manual.md) | install → operate, end to end |
| [Setup](doc/운영가이드/setup.md) | environment and install summary |
| [Collectors](doc/운영가이드/기술참조/collectors.md) | per-collector probes and metrics |
| [Metric list](doc/운영가이드/기술참조/metric_list.md) | implementation status of every metric |
| [Storage](doc/운영가이드/기술참조/storage.md) | InfluxDB schema and tiered file layout |
| [Troubleshooting](doc/운영가이드/troubleshooting.md) | common failures |
| [Changelog](CHANGELOG.md) | notable changes, newest first |

## Testing

The eBPF collectors need a Linux node with root and bcc. Everything else — schema validation, the tiered local
store and the cardinality report — is unit-tested and runs anywhere:

```bash
pip install -r tests/requirements.txt
python -m pytest tests
```

CI runs these tests on Python 3.10–3.12, plus `bash -n` on the shell scripts, for every push and pull request.

## Layout

```
main.py                 entry point / collection manager (asyncio)
config/config.yaml      configuration
src/collectors/         network, syscall, cpu, memory, filesystem, process, container, gpu
src/storage/            influxdb_schema (schema + validator), influxdb_writer, tiered_storage, file_writer
src/utils/              node_info, logger
scripts/                setup_master.sh, setup_agent.sh, systemd unit, Grafana dashboard, cardinality_report.py
tests/                  unit tests (no eBPF or root needed)
doc/                    manuals (Korean)
```

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Please report security problems privately as described in [SECURITY.md](SECURITY.md).

## Acknowledgement

This software is an output of the project **RS-2025-02215256** "AI 반도체 클라우드를 위한 고속 병렬 파일시스템 기술개발"
(AI-semiconductor cloud high-performance parallel file system), funded by the Institute of Information & Communications
Technology Planning & Evaluation (IITP), Republic of Korea, led by the Korea Electronics Technology Institute (KETI).
Nanum Technology develops the eBPF-based metric collection and structured management module.

## License

[MIT](LICENSE)
