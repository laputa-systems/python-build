"""Deterministic output checks for the JIT-sensitive pyperformance kernels."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from benchmarks.workloads import jit_kernels
from benchmarks.workloads.registry import BY_NAME, JIT_SET


class JitKernelTests(unittest.TestCase):
    def test_registry_freezes_the_jit_set_to_the_kernel_table(self) -> None:
        self.assertEqual(len(JIT_SET), 16)
        self.assertEqual(tuple(jit_kernels._KERNELS), JIT_SET)
        for name in JIT_SET:
            with self.subTest(name=name):
                workload = BY_NAME[name]
                self.assertEqual(workload.category, "interpreter")
                self.assertEqual(workload.module, "jit_kernels")

    def test_digests_are_stable_across_two_runs(self) -> None:
        for name in JIT_SET:
            with self.subTest(name=name):
                first = jit_kernels.run(name)
                second = jit_kernels.run(name)
                self.assertEqual(first["digest"], second["digest"])
                self.assertEqual(first["operation_count"], second["operation_count"])
                self.assertGreater(first["operation_count"], 0)

    def test_perturbed_kernel_output_changes_the_digest(self) -> None:
        stable = jit_kernels._digest([("fannkuch", 30)])
        self.assertEqual(stable, jit_kernels._digest([("fannkuch", 30)]))
        self.assertNotEqual(stable, jit_kernels._digest([("fannkuch", 31)]))
        self.assertNotEqual(jit_kernels._digest([b"P6 100 100 255\n"]),
                            jit_kernels._digest([b"P6 100 100 255\n\x00"]))

    def test_wheel_digest_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sums = Path(temporary) / "SHA256SUMS"
            sums.write_text("0" * 64 + "  " + jit_kernels.PYPERFORMANCE_WHEEL.name + "\n")
            with self.assertRaisesRegex(jit_kernels.WorkloadError, "digest mismatch"):
                jit_kernels.verify_wheel(jit_kernels.PYPERFORMANCE_WHEEL, sums)

    def test_unknown_kernel_raises(self) -> None:
        with self.assertRaisesRegex(jit_kernels.WorkloadError, "unknown JIT kernel"):
            jit_kernels.run("not_a_kernel")


if __name__ == "__main__":
    unittest.main()
