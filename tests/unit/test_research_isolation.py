"""The recording server installs without the research extra: nothing outside
quanta.research may need numpy."""

from __future__ import annotations

import subprocess
import sys

SNIPPET = """
import sys
sys.modules["numpy"] = None  # import numpy → ImportError
import quanta.cli, quanta.recorder.service, quanta.ui.app, quanta.lake.daily, quanta.lake.checks
from typer.testing import CliRunner
res = CliRunner().invoke(quanta.cli.app, ["--help"])
assert res.exit_code == 0, res.output
res = CliRunner().invoke(quanta.cli.app, ["research", "--help"])
assert res.exit_code == 0, res.output
print("ok")
"""


def test_core_runs_without_numpy() -> None:
    out = subprocess.run(
        [sys.executable, "-c", SNIPPET], capture_output=True, text=True, check=False
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("ok")
