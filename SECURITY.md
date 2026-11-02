# Security policy

The collector runs as root and loads eBPF programs into the kernel, so security reports are handled with priority.

## Reporting a vulnerability

Please **do not open a public issue**. Report it privately through GitHub:
**Security → Report a vulnerability** on this repository. Include the affected commit, steps to reproduce and the impact.

## Supported versions

Fixes are made on the latest `main` only.

## Deployment notes

- `config/config.yaml` holds the InfluxDB token. Keep it readable by root only (`chmod 600 config/config.yaml`)
  and never commit a real token.
- `scripts/setup_master.sh` ships with password placeholders (`<INFLUXDB_PASSWORD>`). Set your own before running it.
- Expose InfluxDB (8086) and Grafana (3000) to the management network only.
