"""Contract tests for perf-gate-ready's host-readiness-only decision."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

LANE = Path(__file__).resolve().parents[1]
TOOL = LANE / "tools" / "perf-gate-ready"
_LOADER = SourceFileLoader("perf_gate_ready_test", str(TOOL))
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)

ALPINE = {"architecture": "x86_64", "kernel": "6.6.0", "os_id": "alpine",
          "os_version_id": "3.25.0_alpha20260805", "os_pretty_name": "Alpine Linux edge"}
UBUNTU = {"architecture": "x86_64", "kernel": "6.8.0", "os_id": "ubuntu",
          "os_version_id": "24.04", "os_pretty_name": "Ubuntu 24.04 LTS"}
QUIET = {"min_idle_pct": 99.0, "max_iowait_pct": 0.0, "max_steal_pct": 0.0,
         "swap_pages": 0, "cores": 8, "load1": 0.4, "available_gib": 20.0,
         "disk_free_gib": 100.0, "ac_online": None, "path": "/"}


def collected(identity: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    with mock.patch.object(gate, "host_identity", return_value=identity), \
            mock.patch.object(gate, "sample_cpu", return_value={
                key: QUIET[key] for key in
                ("min_idle_pct", "max_iowait_pct", "max_steal_pct", "swap_pages")}), \
            mock.patch.object(gate.shutil, "disk_usage",
                              return_value=SimpleNamespace(free=100 * gate.GIB)), \
            mock.patch.object(gate, "available_memory_gib", return_value=20.0), \
            mock.patch.object(gate, "ac_online", return_value=None), \
            mock.patch.object(gate.os, "getloadavg", return_value=(0.4, 0.4, 0.4)), \
            mock.patch.object(gate.os, "sched_getaffinity", return_value=range(8)):
        return gate.collect(Path("/"))


class PerfGateReadyTests(unittest.TestCase):
    def test_thresholds_are_unchanged(self):
        self.assertEqual(
            (gate.MIN_IDLE_PERCENT, gate.MAX_IOWAIT_PERCENT, gate.MAX_STEAL_PERCENT,
             gate.MAX_LOAD_PER_CORE, gate.MIN_AVAILABLE_GIB, gate.MIN_DISK_FREE_GIB),
            (95.0, 2.0, 0.2, 0.7, 4.0, 20.0),
        )

    def test_quiet_alpine_host_passes_and_records_its_identity(self):
        metrics, identity = collected(ALPINE)
        self.assertEqual(gate.classify(metrics), [])
        self.assertEqual(identity, ALPINE)

    def test_host_identity_does_not_change_the_decision(self):
        alpine_metrics, _ = collected(ALPINE)
        ubuntu_metrics, _ = collected(UBUNTU)
        self.assertEqual(gate.classify(alpine_metrics), gate.classify(ubuntu_metrics))
        busy = {**alpine_metrics, "min_idle_pct": 80.0, "max_steal_pct": 0.5}
        self.assertTrue(any("CPU idle" in failure for failure in gate.classify(busy)))
        self.assertTrue(any("CPU steal" in failure for failure in gate.classify(busy)))

    def test_gnu_toolchain_lock_is_not_part_of_host_readiness(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "rust-cpython" / "linux-toolchain.lock.json"
            lock.parent.mkdir()
            lock.write_text(json.dumps({"provisional_hosts": [
                {"os_version_id": "not-a-linux-host"}]}), encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(temporary)
                metrics, identity = collected(ALPINE)
            finally:
                os.chdir(previous)
        self.assertEqual(gate.classify(metrics), [])
        self.assertEqual(identity["os_id"], "alpine")

    def test_non_x86_64_host_is_rejected(self):
        with mock.patch.object(gate.platform, "machine", return_value="aarch64"), \
                mock.patch.object(gate.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "native Linux x86_64"):
                gate.collect(Path("/"))

    def test_cli_reports_host_scope_for_pre_and_post_checks(self):
        for args, phase in ((["--json"], "pre"), (["--json", "-1"], "post")):
            with self.subTest(args=args):
                output = io.StringIO()
                with mock.patch.object(gate, "collect",
                                       return_value=(QUIET, ALPINE)), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(gate.main(args), 0)
                report = json.loads(output.getvalue())
                self.assertEqual(report["scope"], "host-readiness")
                self.assertEqual(report["phase"], phase)
                self.assertEqual(report["host"], ALPINE)
                self.assertTrue(report["ok"])

    def test_cli_human_output_declares_the_quiet_host_result(self):
        output = io.StringIO()
        with mock.patch.object(gate, "collect", return_value=(QUIET, ALPINE)), \
                contextlib.redirect_stdout(output):
            self.assertEqual(gate.main([]), 0)
        first, metrics = output.getvalue().splitlines()[:2]
        self.assertEqual(first, "OK perf-gate-ready (pre): quiet-host controls passed")
        self.assertEqual(json.loads(metrics)["min_idle_pct"], 99.0)

    def test_cli_self_test_covers_decision_controls(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(gate.main(["--self-test"]), 0)
        self.assertEqual(output.getvalue().strip(), "PASS gate decision controls")


if __name__ == "__main__":
    unittest.main()
