"""Acceptance failures and explicit compiler routes in the Serc census."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import serc_parity


class CensusTests(unittest.TestCase):
    def test_refusals_panics_and_missing_results_cannot_hide_as_zero_mismatches(self):
        for oracle, serc, expected in (
            ("refused", "compiled", "oracle_refused"),
            ("refused", "panic", "serc_error"),
            ("match", "refused", "serc_refused"),
            ("match", "panic", "serc_error"),
            ("match", "error", "serc_error"),
            ("match", None, "serc_error"),
        ):
            with self.subTest(oracle=oracle, serc=serc):
                record = {"file": "case.py", "optimize": 0, "status": oracle,
                          "error": "SyntaxError"}
                outcomes = {} if serc is None else {("case.py", 0): (serc, "reason")}
                result = serc_parity.census(
                    [record], [Path("case.py")], {"0": {"exit": 0}}, outcomes
                )
                self.assertEqual(result["mismatch_total"], 1)
                self.assertEqual(result["classes"], {expected: 1})

    def test_explicit_cpython_route_is_separate_from_exact_compilation(self):
        result = serc_parity.census(
            [{"file": "case.py", "optimize": 0, "status": "no_output"}],
            [Path("case.py")], {"0": {"exit": 0}},
            {("case.py", 0): ("routed", "control flow leaves a finally body")},
        )
        self.assertEqual(result["mismatch_total"], 0)
        self.assertEqual(result["exact_matches"], 0)
        self.assertEqual(result["serc_routes"]["count"], 1)
        self.assertEqual(result["serc_refused"]["count"], 0)

    def test_cli_route_and_unexpected_failure_remain_distinguishable(self):
        source = Path("case.py")
        self.assertEqual(serc_parity.classify_serc(
            1, "failed to compile case.py: this source requires CPython's compiler: finally", source
        ), ("routed", "this source requires CPython's compiler: finally"))
        self.assertEqual(serc_parity.classify_serc(101, "panicked at source.rs", source)[0], "panic")
        self.assertEqual(serc_parity.classify_serc(-11, "", source)[0], "error")

    def test_valid_mode_fixture_cannot_pass_by_routing_around_serc(self):
        result = serc_parity.census(
            [{"file": "case.py", "optimize": 0, "status": "no_output"}],
            [Path("case.py")], {"0": {"exit": 0}},
            {("case.py", 0): ("routed", "requires CPython")},
            require_serc=frozenset({"case.py"}),
        )
        self.assertEqual(result["mismatch_total"], 1)
        self.assertEqual(result["classes"], {"unexpected_route": 1})


if __name__ == "__main__":
    unittest.main()
