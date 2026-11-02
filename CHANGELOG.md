# Changelog

Notable changes, newest first. Dates are when a change landed in the project's internal repository;
the public history on GitHub starts on 2026-10-13. Measured numbers come from the project's test nodes.

## Unreleased

### Added
- Unit tests for the schema validator, the tiered local store and the cardinality report (`tests/`).
  They need neither eBPF nor root.
- GitHub Actions CI — byte-compile, config/dashboard validation and unit tests on Python 3.10–3.12, `bash -n` on the shell scripts.
- Contributing guide, security policy, issue and pull request templates.

## 2026-10-22 — Process-level aggregation

### Changed
- Memory and filesystem collectors aggregate in the kernel by process name instead of PID
  (`aggregate_by: process_name`, the default; `pid` restores per-PID keys).
  Unique keys per 10-second cycle: memory 112 → 61, filesystem 94 → 38.
- `process_name` tag added to `memory_metrics` and `filesystem_metrics`.

## 2026-10-13 — Second-year feature set (first public release)

### Added
- Network: UDP and ICMP, TCP RTT, retransmits and loss rate, rule-based inbound DDoS flag (packets/s and unique sources).
- CPU: per-core user / system / idle / iowait time and SoftIRQ count.
- Filesystem: unlink count and block-layer I/O (operations, bytes, issue → complete latency).
- InfluxDB schema validation (`influxdb.schema_mode: off | warn | strict`) and bounded write retry.
- `gpu_metrics` schema (NVML, disabled by default).
- `scripts/cardinality_report.py` — series count per measurement and tag from local JSONL, with what-if options.

### Changed
- The network collector folds ephemeral ports (≥ `ephemeral_port_min`, default 32768) into `eph` inside the kernel map key.
  Daily series on a test node: about 450,000 → 153.

### Fixed
- Converting JSONL to Parquet kept only the last hour of each day (3.9 % of rows retained).
  A day is now converted only after it is complete, merged into an existing Parquet file, and written through a temporary file.

## 2026-08-25

### Fixed
- `collector_info` (node id, hostname, IP) in JSONL records was always empty.

## 2026-06-05

### Fixed
- syscall counts missing under load; missing eBPF rows in memory, process and filesystem; CPU context switches not attached;
  container `cpu_usage_ns` parsing — caused by BCC map truthiness and kprobes that were never attached.

### Added
- Network send/receive (`tcp_sendmsg`, `tcp_cleanup_rbuf`) with 4-tuple tags (`src_ip`, `dst_ip`, `sport`, `dport`) and `direction`.
- `storage.json_split` — `per_cycle`, `hourly` or `daily` JSONL layout.
- Snap and virtual filesystem mounts excluded from filesystem usage.

## 2025 — First-year release

- eBPF collectors for network, syscalls, CPU, memory, filesystem, processes and containers; GPU collector stub.
- InfluxDB 2.x writer, tiered local store (JSONL → Parquet → Zstandard), Grafana dashboard, master and agent setup scripts.
