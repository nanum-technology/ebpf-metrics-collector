# Contributing

Issues and pull requests are welcome, in English or Korean (한국어 이슈 · PR 도 환영합니다).

## Reporting a problem

Open an issue and include:

- OS and kernel (`uname -r`), Python version, bcc version (`dpkg -l python3-bpfcc`)
- which collector is involved and the relevant part of `config/config.yaml` — **remove tokens and passwords**
- log lines from `journalctl -u ebpf-collector` around the failure

Security problems: see [SECURITY.md](SECURITY.md) and do not open a public issue.

## Development setup

The eBPF collectors need Linux, root and bcc, so they can only be exercised on a real node
(see the [user manual](doc/운영가이드/user_manual.md)). Everything else — schema validation,
the tiered local store and the cardinality report — is covered by unit tests that run anywhere:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r tests/requirements.txt
python -m pytest tests
```

## Pull requests

- One change per pull request. Say what it fixes and how you verified it (unit tests, or the node and kernel you ran it on).
- **Schema changes travel with the collector change.** A new tag or field must be added to
  `src/storage/influxdb_schema.py` in the same pull request — with `schema_mode: strict` anything undeclared is dropped.
  A field's type cannot change once data is written to InfluxDB; add a new field name instead.
- **Mind cardinality.** Every new tag multiplies the number of InfluxDB series. Compare
  `scripts/cardinality_report.py` output before and after on a node's local JSONL.
- Shell scripts stay bash with LF line endings; CI runs `bash -n` on them.
- CI (GitHub Actions) must pass.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
