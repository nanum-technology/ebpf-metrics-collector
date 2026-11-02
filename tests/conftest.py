# tests/conftest.py
#
# Unit tests for the parts of the collector that run without eBPF (no bcc, no root):
# the InfluxDB schema validator, the tiered local store and the cardinality report.
# The eBPF collectors themselves can only be verified on a Linux agent node.

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
