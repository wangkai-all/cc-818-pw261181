from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bss_lab.analysis import AnalysisError, analyze, run_audit
from bss_lab.linalg import eigh_jacobi, match_series, symmetric_orthogonalize


def base_config(**overrides):
    cfg = {
        "scenario": "independent",
        "seed": 818,
        "n_samples": 1200,
        "n_channels": 4,
        "n_sources": 3,
        "n_starts": 2,
        "max_iter": 90,
    }
    cfg.update(overrides)
    return cfg


class LinearAlgebraTests(unittest.TestCase):
    def test_eigen_decomposition_reconstructs_symmetric_matrix(self):
        a = [[4.0, 1.0, -0.4], [1.0, 3.0, 0.2], [-0.4, 0.2, 2.0]]
        vals, vecs = eigh_jacobi(a)
        for k, lam in enumerate(vals):
            v = [vecs[i][k] for i in range(3)]
            av = [sum(a[i][j] * v[j] for j in range(3)) for i in range(3)]
            for i in range(3):
                self.assertAlmostEqual(av[i], lam * v[i], places=7)
        self.assertGreaterEqual(vals[0], vals[-1])

    def test_symmetric_orthogonalization(self):
        w = [[0.7, 0.2], [-0.4, 0.9]]
        out = symmetric_orthogonalize(w)
        product = [
            [sum(out[i][k] * out[j][k] for k in range(2)) for j in range(2)]
            for i in range(2)
        ]
        for i in range(2):
            for j in range(2):
                self.assertAlmostEqual(product[i][j], 1.0 if i == j else 0.0, places=8)

    def test_match_is_invariant_to_order_and_sign(self):
        reference = [
            [math.sin(i / 9.0) for i in range(120)],
            [math.cos(i / 13.0) for i in range(120)],
        ]
        # Deliberately reverse order and flip signs.
        estimated = [[-2.0 * x for x in reference[1]], [3.0 * x for x in reference[0]]]
        matches = match_series(estimated, reference)
        by_est = {m["estimated_index"]: m for m in matches}
        self.assertEqual(by_est[0]["reference_index"], 1)
        self.assertEqual(by_est[0]["sign"], -1)
        self.assertEqual(by_est[1]["reference_index"], 0)
        self.assertEqual(by_est[1]["sign"], 1)
        self.assertGreater(by_est[0]["abs_correlation"], 0.999)


class AnalysisBehaviorTests(unittest.TestCase):
    def test_independent_sources_separate_and_reconstruct(self):
        result = analyze(base_config())
        a = result["analysis"]
        self.assertEqual(a["recovery"]["label"], "matched")
        self.assertGreater(a["recovery"]["min_abs_correlation"], 0.90)
        self.assertGreater(a["training"]["metrics"]["mean_r2"], 0.98)
        self.assertGreater(a["evaluation"]["recalibrated"]["metrics"]["mean_r2"], 0.98)

    def test_correlated_counterexample_can_reconstruct_without_source_recovery(self):
        result = analyze(base_config(scenario="correlated"))
        a = result["analysis"]
        self.assertGreater(a["source_dependence"]["max_abs_pearson"], 0.55)
        self.assertGreater(a["training"]["metrics"]["mean_r2"], 0.95)
        self.assertLess(a["recovery"]["min_abs_correlation"], 0.90)
        codes = {s["code"] for s in a["interpretation"]["statuses"]}
        self.assertIn("independence_violated", codes)

    def test_rank_deficiency_is_diagnosed(self):
        result = analyze(base_config(scenario="rankdef"))
        d = result["diagnostics"]
        self.assertLessEqual(d["effective_rank"], 2)
        self.assertLessEqual(result["analysis"]["rank"]["component_count"], 2)
        codes = {s["code"] for s in result["analysis"]["interpretation"]["statuses"]}
        self.assertIn("rank_deficient", codes)

    def test_constant_and_missing_diagnostics(self):
        constant = analyze(base_config(scenario="constant"))
        self.assertIn(2, constant["diagnostics"]["constant_channels"])
        self.assertNotIn(2, constant["analysis"]["used_channels"])

        missing = analyze(base_config(scenario="missing"))
        self.assertGreater(missing["diagnostics"]["total_missing_cells"], 0)
        self.assertGreaterEqual(missing["diagnostics"]["train_complete_samples"], 32)
        # Missing values must remain null in serialized public segment data.
        raw = missing["dataset"]["channels"][0]["values"]
        self.assertTrue(any(v is None for v in raw))

    def test_channel_permutation_requires_explicit_recalibration(self):
        result = analyze(base_config(scenario="permutation", swap_enabled=True, swap_channels=[0, 1]))
        ev = result["analysis"]["evaluation"]
        self.assertIsNotNone(ev["naive"])
        naive_r2 = ev["naive"]["metrics"]["mean_r2"]
        recal_r2 = ev["recalibrated"]["metrics"]["mean_r2"]
        self.assertLess(naive_r2, recal_r2 - 0.08)
        self.assertGreater(recal_r2, 0.90)
        self.assertEqual(ev["channel_map"], [1, 0, 2, 3])

    def test_known_injected_source_is_matched(self):
        cfg = base_config(
            injection={"enabled": True, "kind": "pulse", "amplitude": 1.15, "spatial": "gradient"}
        )
        result = analyze(cfg)
        k = len(result["dataset"]["true_sources"])
        injected_index = k - 1
        self.assertEqual(result["dataset"]["true_sources"][injected_index]["id"], "injected")
        match = next(m for m in result["analysis"]["matching"]["train"] if m["reference_index"] == injected_index)
        self.assertGreater(match["abs_correlation"], 0.95)

    def test_hiding_channels_changes_dimensions(self):
        result = analyze(base_config(visible_channels=[0, 1, 2]))
        self.assertEqual(result["analysis"]["used_channels"], [0, 1, 2])
        self.assertEqual(result["diagnostics"]["n_channels_visible"], 3)

    def test_impossible_time_range_raises_actionable_error(self):
        with self.assertRaises(AnalysisError):
            analyze(base_config(train_start=900, train_end=100))

    def test_audit_suite_has_no_critical_failure(self):
        audit = run_audit()
        failures = [r for r in audit["results"] if not r["ok"]]
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
