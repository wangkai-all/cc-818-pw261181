"""Dependency-free blind source separation analysis pipeline.

Order of operations is intentional: dimension/missing/constant/rank diagnostics
are produced before whitening or ICA.  Every estimated component is then
matched to synthetic ground truth by optimal absolute correlation assignment;
sign and scale are never treated as source changes.
"""
from __future__ import annotations

import math
from typing import Any

from . import linalg as la
from .synth import generate_dataset

RANK_RTOL = 0.006
NEAR_GAUSSIAN_KURT = 0.35
GOOD_CORR = 0.90
USABLE_CORR = 0.72
STABLE_CORR = 0.88


class AnalysisError(ValueError):
    """Raised when diagnostics show that the requested fit is impossible."""


def _range(config: dict[str, Any], prefix: str, n: int, default_start: int, default_end: int) -> tuple[int, int]:
    start = config.get(f"{prefix}_start", default_start)
    end = config.get(f"{prefix}_end", default_end)
    try:
        lo = max(0, min(n, int(start)))
        hi = max(0, min(n, int(end)))
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"{prefix} 时间段必须是整数样本下标") from exc
    if hi <= lo:
        raise AnalysisError(f"{prefix} 时间段为空或结束位置不大于开始位置")
    if hi - lo < 24:
        raise AnalysisError(f"{prefix} 时间段至少需要 24 个样本")
    return lo, hi


def _missing_ranges(values: list[Any]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    start = None
    for i, value in enumerate(values):
        if value is None and start is None:
            start = i
        elif value is not None and start is not None:
            ranges.append({"start": start, "end": i, "length": i - start})
            start = None
    if start is not None:
        ranges.append({"start": start, "end": len(values), "length": len(values) - start})
    return ranges


def channel_diagnostics(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for i, ch in enumerate(dataset["channels"]):
        values = [v for v in ch["values"] if v is not None]
        missing = len(ch["values"]) - len(values)
        mu = la.mean(values) if values else float("nan")
        sd = la.std(values) if len(values) > 1 else 0.0
        scale_ref = max(1.0, abs(mu), max((abs(v) for v in values), default=0.0))
        is_constant = len(values) == 0 or sd <= 1e-9 * scale_ref or sd < 1e-10
        result.append(
            {
                "channel_index": i,
                "name": ch["name"],
                "unit": ch["unit"],
                "total_samples": len(ch["values"]),
                "available_samples": len(values),
                "missing_count": missing,
                "missing_percent": 100.0 * missing / len(ch["values"]),
                "missing_ranges": _missing_ranges(ch["values"]),
                "mean": mu,
                "std": sd,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "constant": bool(is_constant),
            }
        )
    return result


def diagnose(
    dataset: dict[str, Any],
    selected: list[int],
    train_range: tuple[int, int],
) -> dict[str, Any]:
    all_stats = channel_diagnostics(dataset)
    lo, hi = train_range
    train_len = hi - lo

    selected_stats = [all_stats[i] for i in selected]
    constant_selected = [s["channel_index"] for s in selected_stats if s["constant"]]
    used = [i for i in selected if not all_stats[i]["constant"]]

    complete_count = 0
    if used:
        for t in range(lo, hi):
            if all(dataset["channels"][i]["values"][t] is not None for i in used):
                complete_count += 1

    total_missing = sum(s["missing_count"] for s in all_stats)
    return {
        "n_samples": dataset["n_samples"],
        "n_channels_total": len(dataset["channels"]),
        "n_channels_visible": len(selected),
        "n_channels_used": len(used),
        "candidate_sources": int(dataset["known_structure"]["true_source_count"]),
        "train_range": [lo, hi],
        "train_length": train_len,
        "train_complete_samples": complete_count,
        "total_missing_cells": total_missing,
        "channels": all_stats,
        "constant_channels": [s["channel_index"] for s in all_stats if s["constant"]],
        "constant_selected": constant_selected,
        "used_channels": used,
        "rank": None,
        "eigenvalues": [],
        "effective_rank": None,
        "condition_number": None,
        "warnings": [],
    }


def _train_matrix(dataset: dict[str, Any], used: list[int], lo: int, hi: int):
    complete_global: list[int] = []
    for t in range(lo, hi):
        if all(dataset["channels"][i]["values"][t] is not None for i in used):
            complete_global.append(t)
    if len(complete_global) < 32:
        raise AnalysisError(
            f"训练段完整样本只有 {len(complete_global)} 个；需要至少 32 个。请扩大时间段、恢复缺测通道或屏蔽缺测区。"
        )

    p = len(used)
    x_raw = [[float(dataset["channels"][ch]["values"][t]) for t in complete_global] for ch in used]
    means = [la.mean(row) for row in x_raw]
    scales = [la.std(row) for row in x_raw]
    for i, sd in enumerate(scales):
        if sd < 1e-10:
            raise AnalysisError(f"{dataset['channels'][used[i]]['name']} 近似常量，不能参与白化")
    x_std = [
        [(x_raw[i][n] - means[i]) / scales[i] for n in range(len(complete_global))]
        for i in range(p)
    ]
    return complete_global, x_raw, means, scales, x_std


def _whiten(x_std: la.Matrix, q: int) -> dict[str, Any]:
    cov = la.covariance_rows(x_std)
    eigvals, eigvecs = la.eigh_jacobi(cov)
    lambda0 = max(eigvals[0], 1e-12)
    effective_rank = sum(v > RANK_RTOL * lambda0 and v > 1e-10 for v in eigvals)
    effective_rank = max(1, effective_rank)
    q = max(1, min(q, effective_rank, len(eigvals)))
    p = len(x_std)
    whitening = [
        [eigvecs[i][r] / math.sqrt(max(eigvals[r], 1e-12)) for i in range(p)]
        for r in range(q)
    ]
    white = la.matmul(whitening, x_std)
    condition = eigvals[0] / max(eigvals[effective_rank - 1], 1e-12)
    return {
        "covariance": cov,
        "eigenvalues": eigvals,
        "eigenvectors": eigvecs,
        "effective_rank": effective_rank,
        "condition_number": condition,
        "whitening": whitening,
        "white": white,
        "q": q,
    }


def _fast_ica(white: la.Matrix, seed: int, max_iter: int = 120, tol: float = 1e-6) -> dict[str, Any]:
    q = len(white)
    n = len(white[0]) if q else 0
    rng = __import__("random").Random(seed)
    w = [[rng.gauss(0.0, 1.0) for _ in range(q)] for _ in range(q)]
    w = la.symmetric_orthogonalize(w)

    last_change = float("inf")
    iterations = 0
    converged = False
    for iteration in range(1, max_iter + 1):
        iterations = iteration
        y = la.matmul(w, white)
        w_new = la.zeros(q, q)
        for i in range(q):
            yi = y[i]
            g = [math.tanh(v) for v in yi]
            gp_mean = sum(1.0 - v * v for v in g) / n
            wi = w[i]
            for j in range(q):
                xj = white[j]
                expectation = sum(xj[t] * g[t] for t in range(n)) / n
                w_new[i][j] = expectation - gp_mean * wi[j]
        try:
            w_new = la.symmetric_orthogonalize(w_new)
        except Exception as exc:  # pragma: no cover - numerical guard
            raise AnalysisError("ICA 正交化失败，通常因有效秩不足或样本过少") from exc

        change = 0.0
        for i in range(q):
            dot = abs(sum(w_new[i][j] * w[i][j] for j in range(q)))
            change = max(change, 1.0 - min(1.0, dot))
        w = w_new
        last_change = change
        if change < tol:
            converged = True
            break

    raw_components = la.matmul(w, white)
    c_means = [la.mean(row) for row in raw_components]
    c_scales = [la.std(row) for row in raw_components]
    components = []
    b_norm = []
    for i in range(q):
        sd = c_scales[i]
        if sd < 1e-10:
            sd = 1.0
        components.append([(raw_components[i][t] - c_means[i]) / sd for t in range(n)])
        b_norm.append([w[i][j] / sd for j in range(q)])

    contrast = 0.0
    normal_logcosh = 0.3745748276
    for row in components:
        value = (sum(math.log(math.cosh(max(-8.0, min(8.0, v)))) for v in row) / n - normal_logcosh)
        contrast += value * value
    return {
        "w": w,
        "components": components,
        "component_means": c_means,
        "component_scales": c_scales,
        "b_white_norm": b_norm,
        "iterations": iterations,
        "converged": converged,
        "last_change": last_change,
        "objective": contrast,
        "seed": seed,
    }


def _fit_pipeline(dataset, used, train_lo, train_hi, requested_q, n_starts, max_iter, base_seed):
    complete_global, _, means, scales, x_std = _train_matrix(dataset, used, train_lo, train_hi)
    pre = _whiten(x_std, requested_q if isinstance(requested_q, int) else len(used))
    q = pre["q"]

    runs = []
    for r in range(max(1, int(n_starts))):
        run = _fast_ica(pre["white"], base_seed + 100003 * (r + 1), max_iter=max_iter)
        run["start_index"] = r
        runs.append(run)

    viable = [r for r in runs if all(s > 1e-8 for s in r["component_scales"])]
    pool = viable or runs
    best = max(range(len(pool)), key=lambda idx: (pool[idx]["converged"], pool[idx]["objective"]))
    best_run = pool[best]
    best_index_global = runs.index(best_run)

    # Match every restart to the selected reference before summarizing.
    stability_pairs = []
    run_summaries = []
    aligned_runs = []
    for r_idx, run in enumerate(runs):
        matches = la.match_series(run["components"], best_run["components"])
        score = sum(m["abs_correlation"] for m in matches) / max(1, len(matches))
        stability_pairs.append(score)
        aligned_runs.append(la.aligned_copy(run["components"], matches, q))
        run_summaries.append(
            {
                "start_index": r_idx,
                "seed": run["seed"],
                "iterations": run["iterations"],
                "converged": run["converged"],
                "last_change": run["last_change"],
                "objective": run["objective"],
                "match_to_selected": score,
                "is_selected": r_idx == best_index_global,
            }
        )

    pairwise = [[1.0 if i == j else 0.0 for j in range(len(runs))] for i in range(len(runs))]
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            m = la.match_series(runs[i]["components"], runs[j]["components"])
            score = sum(x["abs_correlation"] for x in m) / max(1, len(m))
            pairwise[i][j] = pairwise[j][i] = score

    b_sensor = la.matmul(best_run["w"], pre["whitening"])  # q x p
    a_standard = la.solve_linear_fit(x_std, best_run["components"])  # p x q
    a_raw = [[scales[i] * a_standard[i][j] for j in range(q)] for i in range(len(used))]
    params = {
        "used_channels": used,
        "train_range": [train_lo, train_hi],
        "train_complete_global": complete_global,
        "mean": means,
        "scale": scales,
        "whitening": pre["whitening"],
        "w": best_run["w"],
        "component_mean": best_run["component_means"],
        "component_scale": best_run["component_scales"],
        "b_sensor": b_sensor,
        "a_standard": a_standard,
        "a_raw": a_raw,
        "q": q,
    }
    rank_info = {
        "covariance": pre["covariance"],
        "eigenvalues": pre["eigenvalues"],
        "effective_rank": pre["effective_rank"],
        "condition_number": pre["condition_number"],
        "requested_q": requested_q,
        "q": q,
    }
    stability = {
        "runs": run_summaries,
        "pairwise_match": pairwise,
        "mean_match_to_selected": sum(x for i, x in enumerate(stability_pairs) if i != best_index_global)
        / max(1, len(stability_pairs) - 1),
        "min_match_to_selected": min((x for i, x in enumerate(stability_pairs) if i != best_index_global), default=1.0),
        "aligned_components": aligned_runs,
    }
    return params, rank_info, stability, best_run["components"], complete_global


def recalibrate_params(params: dict[str, Any], current_to_training: list[int]) -> dict[str, Any]:
    """Permute channel-axis parameters after an explicit channel transposition."""
    p = len(params["used_channels"])
    if sorted(current_to_training) != list(range(p)):
        raise AnalysisError("通道重标定映射必须是分析通道位置的一个排列")
    mapping = current_to_training
    whitening = [[params["whitening"][r][mapping[c]] for c in range(p)] for r in range(params["q"])]
    a_standard = [[params["a_standard"][mapping[i]][j] for j in range(params["q"])] for i in range(p)]
    return {
        **params,
        "mean": [params["mean"][mapping[c]] for c in range(p)],
        "scale": [params["scale"][mapping[c]] for c in range(p)],
        "whitening": whitening,
        "a_standard": a_standard,
        "a_raw": [
            [params["scale"][mapping[i]] * a_standard[i][j] for j in range(params["q"])]
            for i in range(p)
        ],
        "recalibrated_mapping": mapping,
    }


def apply_pipeline(dataset: dict[str, Any], params: dict[str, Any], lo: int, hi: int) -> dict[str, Any]:
    used = params["used_channels"]
    p = len(used)
    q = params["q"]
    length = hi - lo
    observed: list[list[float | None]] = [
        list(dataset["channels"][ch]["values"][lo:hi]) for ch in used
    ]
    components: list[list[float | None]] = [[None for _ in range(length)] for _ in range(q)]
    reconstruction: list[list[float | None]] = [[None for _ in range(length)] for _ in range(p)]
    residual: list[list[float | None]] = [[None for _ in range(length)] for _ in range(p)]
    complete_local: list[int] = []
    y_complete: la.Matrix = [[] for _ in range(q)]

    for local in range(length):
        raw = [observed[i][local] for i in range(p)]
        if any(v is None for v in raw):
            continue
        complete_local.append(local)
        raw_f = [float(v) for v in raw]  # type: ignore[arg-type]
        x_std = [(raw_f[i] - params["mean"][i]) / params["scale"][i] for i in range(p)]
        white = la.matvec(params["whitening"], x_std)
        c_raw = la.matvec(params["w"], white)
        y = [
            (c_raw[i] - params["component_mean"][i]) / max(params["component_scale"][i], 1e-12)
            for i in range(q)
        ]
        for i in range(q):
            components[i][local] = y[i]
            y_complete[i].append(y[i])
        xs_hat = la.matvec(params["a_standard"], y)
        for i in range(p):
            hat = params["mean"][i] + params["scale"][i] * xs_hat[i]
            reconstruction[i][local] = hat
            residual[i][local] = raw_f[i] - hat

    channel_metrics = []
    r2_values = []
    nrmse_values = []
    for i, ch_index in enumerate(used):
        obs_complete = [observed[i][t] for t in complete_local]
        hat_complete = [reconstruction[i][t] for t in complete_local]
        res_complete = [residual[i][t] for t in complete_local]
        obs_f = [float(v) for v in obs_complete]  # type: ignore[arg-type]
        hat_f = [float(v) for v in hat_complete]  # type: ignore[arg-type]
        mu = la.mean(obs_f)
        sse = sum(r * r for r in res_complete)
        sst = sum((v - mu) ** 2 for v in obs_f)
        rmse = math.sqrt(sse / max(1, len(obs_f)))
        sd = math.sqrt(max(0.0, sst / max(1, len(obs_f))))
        r2 = None if sst <= 1e-18 else 1.0 - sse / sst
        nrmse = None if sd <= 1e-12 else rmse / sd
        corr = la.corrcoef(obs_f, hat_f)
        if r2 is not None:
            r2_values.append(max(-1.0, min(1.0, r2)))
        if nrmse is not None:
            nrmse_values.append(nrmse)
        channel_metrics.append(
            {
                "channel_index": ch_index,
                "name": dataset["channels"][ch_index]["name"],
                "unit": dataset["channels"][ch_index]["unit"],
                "complete_samples": len(obs_f),
                "rmse": rmse,
                "nrmse_std": nrmse,
                "r2": r2,
                "observed_reconstruction_correlation": corr,
                "residual_lag1": _lag1(res_complete),
            }
        )

    return {
        "range": [lo, hi],
        "complete_samples": len(complete_local),
        "complete_local_indices": complete_local,
        "observed": observed,
        "components": components,
        "reconstruction": reconstruction,
        "residual": residual,
        "channel_metrics": channel_metrics,
        "metrics": {
            "mean_r2": la.mean(r2_values) if r2_values else None,
            "mean_nrmse_std": la.mean(nrmse_values) if nrmse_values else None,
            "complete_fraction": len(complete_local) / max(1, length),
        },
        "_y_complete": y_complete,
    }


def _lag1(values: list[float]) -> float | None:
    if len(values) < 4:
        return None
    return la.corrcoef(values[:-1], values[1:])


def match_to_truth(
    dataset: dict[str, Any],
    used: list[int],
    y_complete: la.Matrix,
    complete_global: list[int],
) -> dict[str, Any]:
    q = len(y_complete)
    k = len(dataset["true_sources"])
    truth_complete = [
        [float(src["values"][t]) for t in complete_global] for src in dataset["true_sources"]
    ]
    matches = la.match_series(y_complete, truth_complete)
    corr_matrix = [[0.0 for _ in range(k)] for _ in range(q)]
    gains = [None for _ in range(q)]
    for i in range(q):
        for j in range(k):
            corr_matrix[i][j] = la.corrcoef(y_complete[i], truth_complete[j])
    detailed = []
    for m in matches:
        i = m["estimated_index"]
        j = m["reference_index"]
        gain = intercept = None
        if j is not None:
            y = y_complete[i]
            s = truth_complete[j]
            my, ms = la.mean(y), la.mean(s)
            den = sum((v - my) ** 2 for v in y)
            gain = sum((y[n] - my) * (s[n] - ms) for n in range(len(y))) / max(den, 1e-30)
            intercept = ms - gain * my
            gains[i] = gain
        detailed.append({**m, "gain_to_truth": gain, "intercept_to_truth": intercept})

    aligned_components: list[list[float | None]] = [[None] * 0 for _ in range(k)]  # replaced by caller
    mixing_true_used = [[dataset["mixing_true"][ch][j] for j in range(k)] for ch in used]
    mixing_est_aligned: list[list[float | None]] = [
        [None for _ in range(k)] for _ in range(len(used))
    ]
    mixing_metrics = []
    a_raw = None  # supplied via closure helper below? This function receives no params.
    return {
        "matches": detailed,
        "correlation_matrix": corr_matrix,
        "truth_complete": truth_complete,
        "gains": gains,
        "mixing_true_used": mixing_true_used,
        "_mixing_est_aligned": mixing_est_aligned,
        "mixing_metrics": mixing_metrics,
    }


def build_truth_aligned_series(segment: dict[str, Any], match_info: dict[str, Any], k: int):
    length = segment["range"][1] - segment["range"][0]
    out: list[list[float | None]] = [[None for _ in range(length)] for _ in range(k)]
    for m in match_info["matches"]:
        j = m["reference_index"]
        i = m["estimated_index"]
        gain = m.get("gain_to_truth")
        intercept = m.get("intercept_to_truth")
        if j is None or gain is None or intercept is None:
            continue
        for t, value in enumerate(segment["components"][i]):
            if value is not None:
                out[j][t] = gain * value + intercept
    return out


def add_mixing_alignment(params: dict[str, Any], match_info: dict[str, Any]) -> None:
    k = len(match_info["mixing_true_used"][0]) if match_info["mixing_true_used"] else 0
    aligned = [[None for _ in range(k)] for _ in range(len(params["used_channels"]))]
    metrics = []
    for m in match_info["matches"]:
        j = m["reference_index"]
        i = m["estimated_index"]
        gain = m.get("gain_to_truth")
        if j is None or gain is None or abs(gain) < 1e-8:
            continue
        est_col = [params["a_raw"][r][i] / gain for r in range(len(params["used_channels"]))]
        for r, value in enumerate(est_col):
            aligned[r][j] = value
        true_col = [match_info["mixing_true_used"][r][j] for r in range(len(params["used_channels"]))]
        cos = sum(a * b for a, b in zip(est_col, true_col)) / max(
            math.sqrt(sum(a * a for a in est_col) * sum(b * b for b in true_col)), 1e-30
        )
        rel_norm = math.sqrt(sum(a * a for a in est_col)) / max(math.sqrt(sum(b * b for b in true_col)), 1e-30)
        metrics.append(
            {
                "source_index": j,
                "component_index": i,
                "cosine_similarity": cos,
                "relative_column_norm": rel_norm,
            }
        )
    match_info["_mixing_est_aligned"] = aligned
    match_info["mixing_metrics"] = metrics


def _source_dependence(dataset: dict[str, Any]) -> dict[str, Any]:
    sources = dataset["true_sources"]
    k = len(sources)
    corr = [[1.0 if i == j else 0.0 for j in range(k)] for i in range(k)]
    max_corr = 0.0
    for i in range(k):
        for j in range(i + 1, k):
            c = la.corrcoef(sources[i]["values"], sources[j]["values"])
            corr[i][j] = corr[j][i] = c
            max_corr = max(max_corr, abs(c))
    return {"pearson": corr, "max_abs_pearson": max_corr}


def _component_diagnostics(y_complete: la.Matrix) -> dict[str, Any]:
    q = len(y_complete)
    kurt = [la.kurtosis_excess(row) for row in y_complete]
    mi = [[1.0 if i == j else 0.0 for j in range(q)] for i in range(q)]
    max_mi = 0.0
    for i in range(q):
        for j in range(i + 1, q):
            value = la.mutual_information(y_complete[i], y_complete[j], bins=10)
            mi[i][j] = mi[j][i] = value
            max_mi = max(max_mi, value)
    return {"excess_kurtosis": kurt, "normalized_mutual_information": mi, "max_pairwise_mi": max_mi}


def _recovery_summary(match_info: dict[str, Any]) -> dict[str, Any]:
    scores = sorted(m["abs_correlation"] for m in match_info["matches"])
    matched_scores = [m["abs_correlation"] for m in match_info["matches"] if m["reference_index"] is not None]
    # Margin against the best non-assigned truth source.
    margins = []
    corr_matrix = match_info["correlation_matrix"]
    for m in match_info["matches"]:
        i = m["estimated_index"]
        j = m["reference_index"]
        if j is None:
            continue
        others = [abs(corr_matrix[i][h]) for h in range(len(corr_matrix[i])) if h != j]
        margins.append(m["abs_correlation"] - (max(others) if others else 0.0))
    min_score = min(matched_scores, default=0.0)
    mean_score = sum(matched_scores) / max(1, len(matched_scores))
    min_margin = min(margins, default=0.0)
    if min_score >= GOOD_CORR and min_margin >= 0.12:
        label = "matched"
    elif min_score >= USABLE_CORR:
        label = "partial"
    else:
        label = "not_identifiable"
    return {
        "label": label,
        "min_abs_correlation": min_score,
        "mean_abs_correlation": mean_score,
        "min_margin": min_margin,
        "per_component": match_info["matches"],
    }


def _interpret(config, dataset, diag, rank_info, recovery, source_dep, comp_diag, stability, eval_info, swap_info):
    items = []

    def add(level, code, title, detail):
        items.append({"level": level, "code": code, "title": title, "detail": detail})

    add("info", "dimensions", f"维度与量纲已处理",
        f"{diag['n_channels_total']} 个传感器、{dataset['n_samples']} 个样本；当前使用 {diag['n_channels_used']} 路，候选真实来源 {diag['candidate_sources']} 个。各通道先按训练段均值/标准差恢复量纲，再做协方差白化。")

    if diag["constant_channels"]:
        add("critical" if not diag["constant_selected"] else "warning", "constant",
            "检测到常量通道",
            f"通道 {[i + 1 for i in diag['constant_channels']]} 方差近似为零，不能提供混合方向；拟合已自动排除。可在网页中屏蔽/确认这些通道。")
    if any(c["missing_count"] for c in diag["channels"]):
        add("warning", "missing", "存在缺测区",
            f"共 {diag['total_missing_cells']} 个通道-样本缺测单元。白化与 ICA 只使用所有在用通道均完整的训练样本；图表和重建在缺测处断开，不把插值观测当作证据。训练段完整样本 {diag['train_complete_samples']} 个。")

    if rank_info["effective_rank"] < diag["candidate_sources"] or rank_info["q"] < diag["candidate_sources"]:
        add("critical", "rank_deficient", "秩不足：不能识别全部来源",
            f"协方差有效信号秩约为 {rank_info['effective_rank']}，本次分量数为 {rank_info['q']}，少于真实/候选来源数 {diag['candidate_sources']}。观测仍可能被低秩混合很好重建，但未观察到的来源方向不可辨识，不能给每个分量强行命名。")
    elif rank_info["condition_number"] > 100:
        add("warning", "ill_conditioned", "混合系统条件数偏大",
            f"保留特征值的条件数约 {rank_info['condition_number']:.1f}，弱方向会放大噪声并降低跨次稳定性。")
    else:
        add("success", "rank_ok", "秩与白化正常",
            f"有效信号秩 {rank_info['effective_rank']}，保留 {rank_info['q']} 个白化方向，条件数约 {rank_info['condition_number']:.1f}。")

    if source_dep["max_abs_pearson"] >= 0.55:
        add("critical", "independence_violated", "独立性反例：真实来源相关",
            f"合成真值的最大绝对相关为 {source_dep['max_abs_pearson']:.2f}。这违反标准 ICA 的来源独立假设；即使重建误差很低，估计分量也可能只是另一组可逆旋转/混合，不能声称恢复了真实来源。")
    gaussian_count = sum(abs(k) < NEAR_GAUSSIAN_KURT for k in comp_diag["excess_kurtosis"])
    gaussian_uncertain = int(dataset["known_structure"].get("near_gaussian_sources", 0) or 0) >= 2 or gaussian_count >= 2
    if gaussian_uncertain:
        add("warning", "near_gaussian", "两个或更多方向近似高斯",
            f"估计分量超额峰度为 {[round(k, 2) for k in comp_diag['excess_kurtosis']]}。高斯方向缺少高阶可辨识信息，起点间旋转可能不稳定；应保留“不确定/仅张成同一子空间”的解释。")

    if recovery["label"] == "uncertain_gaussian":
        add("warning", "recovery_gaussian_uncertain", "数值匹配仅供审计：高斯来源轴原则上不可辨识",
            f"本次有限样本的最低 |相关| 为 {recovery['min_abs_correlation']:.3f}（有限样本判定：{recovery.get('finite_sample_label')}）。但两个近似高斯方向可被任意正交旋转而不改变分布；即使本次种子恰好匹配或多起点收敛，也只能声称张成同一子空间，不能给分量赋予稳定物理身份。")
    elif recovery["label"] == "matched" and not gaussian_uncertain:
        add("success", "recovery_good", "分量已按最优分配匹配到来源",
            f"最低 |相关| 为 {recovery['min_abs_correlation']:.3f}，最小次优间隔 {recovery['min_margin']:.3f}。顺序、符号和尺度已经通过匹配校正；翻转不是来源改变。")
    elif recovery["label"] == "partial":
        add("warning", "recovery_partial", "只能部分匹配来源",
            f"最低 |相关| 为 {recovery['min_abs_correlation']:.3f}。部分来源可能混合、相关或接近高斯；请同时查看稳定性和反例说明。")
    else:
        add("critical", "recovery_failed", "来源恢复不成立",
            f"最优匹配后的最低 |相关| 仅 {recovery['min_abs_correlation']:.3f}。若重建仍然很好，说明分解解释了观测，但没有恢复出生成来源；不要给分量指定物理名称。")

    if stability["min_match_to_selected"] < STABLE_CORR:
        add("warning", "unstable_starts", "多起点稳定性不足",
            f"其他起点与选中结果匹配后的最低平均 |相关| 为 {stability['min_match_to_selected']:.3f}。应报告子空间/重建稳定但分量轴不稳定，而不是选择一次好看的运行。")
    elif gaussian_uncertain:
        add("info", "stable_but_gaussian_ambiguity", "多起点在这一有限样本上稳定，但不能消除高斯旋转歧义",
            f"匹配后的最低平均 |相关| 为 {stability['min_match_to_selected']:.3f}。这说明算法收敛稳定；跨数据实现/种子仍可能在高斯子空间内旋转，不应把它解释成来源身份稳定。")
    else:
        add("success", "stable_starts", "多起点匹配后稳定",
            f"所有起点先做顺序与符号匹配；与选中结果的最低平均 |相关| 为 {stability['min_match_to_selected']:.3f}。")

    if eval_info:
        m = eval_info.get("recalibrated", {}).get("metrics", {})
        nm = eval_info.get("naive", {}).get("metrics") if eval_info.get("naive") else None
        if swap_info and swap_info.get("active"):
            if nm and nm.get("mean_r2") is not None and m.get("mean_r2") is not None and nm["mean_r2"] < m["mean_r2"] - 0.1:
                add("warning", "permutation_naive_fails", "检测到通道换位，未重标定的固定系数失效",
                    f"后续段通道映射为 当前位置→训练位置 {swap_info['current_to_training']}。直接套用训练混合关系的平均 R²={nm['mean_r2']:.3f}；显式重标定后 R²={m['mean_r2']:.3f}。")
            add("success", "permutation_recalibrated", "通道换位已显式重标定",
                "已同步置换均值、尺度、白化矩阵列和混合矩阵行；训练段拟合的系数值本身没有在后续段重新拟合。")
        elif swap_info:
            add("info", "permutation_inactive", "选择了通道换位，但换位通道不在拟合集合中",
                "请取消屏蔽两个被交换的通道；当前分析通道顺序未受影响，因此无需重标定。")
        elif m.get("mean_r2") is not None:
            add("info", "fixed_relation", "后续段固定使用训练关系",
                f"训练段得到的白化/ICA/混合系数在后续段固定，平均 R²={m['mean_r2']:.3f}。")
        if eval_info and eval_info.get("range", [0, 0])[0] < diag["train_range"][1]:
            add("warning", "segment_overlap", "训练段与检验段重叠",
                "当前选择不是严格的“先训练、后固定检验”顺序；重叠区间的指标不能作为时间外推验证，请调整时间段。")

    return {"statuses": items}


def analyze(config: dict[str, Any]) -> dict[str, Any]:
    config = config or {}
    dataset = generate_dataset(config)
    n = dataset["n_samples"]

    selected = config.get("visible_channels")
    if not isinstance(selected, list) or not selected:
        selected = list(range(len(dataset["channels"])))
    selected = sorted({int(i) for i in selected if 0 <= int(i) < len(dataset["channels"])})
    if not selected:
        raise AnalysisError("至少需要保留一个通道")

    train_lo, train_hi = _range(config, "train", n, int(0.06 * n), int(0.55 * n))
    eval_lo, eval_hi = _range(config, "eval", n, int(0.60 * n), int(0.94 * n))

    swap_enabled = bool(config.get("swap_enabled", False))
    swap_channels = config.get("swap_channels")
    swap_info = None
    if swap_enabled and isinstance(swap_channels, list) and len(swap_channels) == 2:
        a, b = int(swap_channels[0]), int(swap_channels[1])
        if a != b and 0 <= a < len(dataset["channels"]) and 0 <= b < len(dataset["channels"]):
            # Simulate cable/order transposition in the later segment only.
            for t in range(eval_lo, eval_hi):
                va = dataset["channels"][a]["values"][t]
                vb = dataset["channels"][b]["values"][t]
                dataset["channels"][a]["values"][t] = vb
                dataset["channels"][b]["values"][t] = va
            swap_info = {"channels": [a, b], "eval_range": [eval_lo, eval_hi], "active": False}

    diag = diagnose(dataset, selected, (train_lo, train_hi))
    used = diag["used_channels"]
    if not used:
        raise AnalysisError("当前可见通道全部为常量或无有效数据；请至少启用一个非常量通道")

    # Complete-case requirement is checked again with a precise message.
    complete = diag["train_complete_samples"]
    if complete < 32:
        raise AnalysisError(f"训练段完整样本只有 {complete} 个；至少需要 32 个所有在用通道均不缺测的样本")

    cc = config.get("component_count", "auto")
    if cc in (None, "auto", "rank"):
        requested_q = len(used)
    else:
        requested_q = max(1, min(len(used), int(cc)))

    n_starts = int(config.get("n_starts", 6))
    max_iter = int(config.get("max_iter", 120))
    seed = int(config.get("seed", 2611818))

    params, rank_info, stability, y_train_complete, train_complete_global = _fit_pipeline(
        dataset, used, train_lo, train_hi, requested_q, n_starts, max_iter, seed
    )
    diag["rank"] = rank_info
    diag["eigenvalues"] = rank_info["eigenvalues"]
    diag["effective_rank"] = rank_info["effective_rank"]
    diag["condition_number"] = rank_info["condition_number"]

    train_segment = apply_pipeline(dataset, params, train_lo, train_hi)
    train_match = match_to_truth(dataset, used, y_train_complete, train_complete_global)
    add_mixing_alignment(params, train_match)
    k_truth = len(dataset["true_sources"])
    train_truth_aligned = build_truth_aligned_series(train_segment, train_match, k_truth)
    component_diag = _component_diagnostics(y_train_complete)
    source_dep = _source_dependence(dataset)
    recovery = _recovery_summary(train_match)
    gaussian_uncertain = int(dataset["known_structure"].get("near_gaussian_sources", 0) or 0) >= 2
    if gaussian_uncertain:
        # A particular finite sample can line up unusually well.  That must
        # not override the structural non-identifiability of a Gaussian pair.
        recovery["finite_sample_label"] = recovery["label"]
        recovery["label"] = "uncertain_gaussian"
        recovery["confidence"] = "Gaussian rotation subspace only; source labels are not identifiable"

    evaluation = None
    if eval_hi > eval_lo:
        fixed_segment = apply_pipeline(dataset, params, eval_lo, eval_hi)
        eval_complete_global = list(range(eval_lo, eval_hi))
        # apply_pipeline only returns local complete indices; map to global.
        eval_global_complete = [eval_lo + i for i in fixed_segment["complete_local_indices"]]
        eval_match = match_to_truth(dataset, used, fixed_segment["_y_complete"], eval_global_complete)
        eval_truth_aligned = build_truth_aligned_series(fixed_segment, eval_match, k_truth)

        current_to_training = list(range(len(used)))
        naive_segment = None
        if swap_info:
            a, b = swap_info["channels"]
            if a in used and b in used:
                swap_info["active"] = True
                pa, pb = used.index(a), used.index(b)
                current_to_training[pa] = pb
                current_to_training[pb] = pa
                swap_info["current_to_training"] = current_to_training
                swap_info["mapping_human"] = [
                    f"当前位置 {pa + 1}（{dataset['channels'][a]['name']}接口）→ 训练位置 {pb + 1}",
                    f"当前位置 {pb + 1}（{dataset['channels'][b]['name']}接口）→ 训练位置 {pa + 1}",
                ]
                recal_params = recalibrate_params(params, current_to_training)
                recal_segment = apply_pipeline(dataset, recal_params, eval_lo, eval_hi)
                recal_match = match_to_truth(dataset, used, recal_segment["_y_complete"], eval_global_complete)
                recal_aligned = build_truth_aligned_series(recal_segment, recal_match, k_truth)
                # The un-recalibrated result is exactly fixed_segment under the
                # original training-axis parameters on swapped current data.
                naive_segment = fixed_segment
                fixed_segment = recal_segment
                eval_match = recal_match
                eval_truth_aligned = recal_aligned
            else:
                swap_info["warning"] = "换位通道不在当前拟合通道集合中，因此没有对拟合结果产生影响"

        evaluation = {
            "range": [eval_lo, eval_hi],
            "recalibrated": _public_segment(fixed_segment, eval_match, eval_truth_aligned),
            "truth_matches": eval_match["matches"],
            "naive": _public_segment(naive_segment, None, None) if naive_segment else None,
            "channel_map": current_to_training,
        }

    interpretation = _interpret(
        config, dataset, diag, rank_info, recovery, source_dep, component_diag,
        stability, evaluation, swap_info
    )

    # Remove internal large/private fields before serialization.
    train_public = _public_segment(train_segment, train_match, train_truth_aligned)
    train_public["match"] = train_match["matches"]
    train_public["correlation_matrix"] = train_match["correlation_matrix"]

    return {
        "ok": True,
        "dataset": dataset,
        "diagnostics": diag,
        "analysis": {
            "selected_channels": selected,
            "used_channels": used,
            "train_range": [train_lo, train_hi],
            "eval_range": [eval_lo, eval_hi],
            "swap": swap_info,
            "rank": {
                "eigenvalues": rank_info["eigenvalues"],
                "effective_rank": rank_info["effective_rank"],
                "condition_number": rank_info["condition_number"],
                "component_count": rank_info["q"],
            },
            "pipeline_matrices": {
                "mean": params["mean"],
                "scale": params["scale"],
                "whitening": params["whitening"],
                "w_ica": params["w"],
                "a_standardized": params["a_standard"],
                "a_raw_original_units": params["a_raw"],
            },
            "training": train_public,
            "evaluation": evaluation,
            "matching": {
                "train": train_match["matches"],
                "correlation_matrix": train_match["correlation_matrix"],
                "mixing_true_used": train_match["mixing_true_used"],
                "mixing_estimated_truth_aligned": train_match["_mixing_est_aligned"],
                "mixing_metrics": train_match["mixing_metrics"],
            },
            "recovery": recovery,
            "source_dependence": source_dep,
            "component_diagnostics": component_diag,
            "stability": {k: v for k, v in stability.items() if k != "aligned_components"},
            "stability_aligned_components": stability["aligned_components"],
            "interpretation": interpretation,
        },
    }


def _public_segment(segment: dict[str, Any] | None, match_info, truth_aligned) -> dict[str, Any] | None:
    if segment is None:
        return None
    out = {k: v for k, v in segment.items() if not k.startswith("_")}
    if match_info is not None:
        out["match"] = match_info["matches"]
        out["correlation_matrix"] = match_info["correlation_matrix"]
    if truth_aligned is not None:
        out["components_truth_aligned"] = truth_aligned
    return out


def run_audit() -> dict[str, Any]:
    """Small deterministic counterexample suite used by the UI and tests."""
    cases = [
        ("independent", "独立非高斯来源"),
        ("correlated", "相关来源反例"),
        ("gaussian", "近似高斯来源反例"),
        ("rankdef", "秩不足反例"),
        ("constant", "常量通道诊断"),
        ("missing", "缺测区诊断"),
        ("permutation", "通道换位重标定"),
    ]
    results = []
    for scenario, label in cases:
        config = {
            "scenario": scenario,
            "seed": 2611818,
            "n_samples": 1200,
            "n_channels": 3 if scenario == "gaussian" else 4,
            "n_sources": 2 if scenario == "gaussian" else 3,
            "n_starts": 3,
            "max_iter": 100,
            "missing_enabled": scenario == "missing",
            "swap_enabled": scenario == "permutation",
            "swap_channels": [0, 1] if scenario == "permutation" else None,
        }
        try:
            result = analyze(config)
            a = result["analysis"]
            d = result["diagnostics"]
            checks = _audit_expectations(scenario, result)
            results.append(
                {
                    "scenario": scenario,
                    "label": label,
                    "ok": all(c["passed"] for c in checks if c["critical"]),
                    "checks": checks,
                    "metrics": {
                        "recovery": a["recovery"]["label"],
                        "min_source_correlation": a["recovery"]["min_abs_correlation"],
                        "mean_train_r2": a["training"]["metrics"]["mean_r2"],
                        "mean_eval_r2": (a.get("evaluation") or {}).get("recalibrated", {}).get("metrics", {}).get("mean_r2"),
                        "naive_eval_r2": (((a.get("evaluation") or {}).get("naive") or {}).get("metrics") or {}).get("mean_r2"),
                        "effective_rank": d["effective_rank"],
                        "stability": a["stability"]["mean_match_to_selected"],
                    },
                }
            )
        except Exception as exc:
            results.append({"scenario": scenario, "label": label, "ok": False, "error": str(exc), "checks": []})
    return {"ok": all(r["ok"] for r in results), "results": results}


def _audit_expectations(scenario: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    a = result["analysis"]
    d = result["diagnostics"]
    checks = []

    def check(code, label, passed, observed, critical=True, explanation=""):
        checks.append({"code": code, "label": label, "passed": bool(passed), "observed": observed,
                       "critical": critical, "explanation": explanation})

    train_r2 = a["training"]["metrics"]["mean_r2"] or -1
    check("reconstruction", "混合重建应解释大部分观测", train_r2 >= 0.90, f"平均 R²={train_r2:.3f}")

    if scenario == "independent":
        score = a["recovery"]["min_abs_correlation"]
        check("recovery", "独立非高斯来源应恢复", score >= 0.84, f"最低 |相关|={score:.3f}")
        check("stability", "多起点匹配后应稳定", a["stability"]["min_match_to_selected"] >= 0.80,
              f"最低稳定性={a['stability']['min_match_to_selected']:.3f}", critical=False)
    elif scenario == "correlated":
        dep = a["source_dependence"]["max_abs_pearson"]
        check("dependence", "应诊断出高来源相关", dep >= 0.55, f"最大 |相关|={dep:.3f}")
        score = a["recovery"]["min_abs_correlation"]
        check("recovery_limited", "相关来源不应被可靠识别", score < 0.90, f"最低匹配 |相关|={score:.3f}", critical=False,
              explanation="低重建误差与低来源恢复可以同时成立")
        check("warning", "应保留独立性失败说明",
              any(s["code"] == "independence_violated" for s in a["interpretation"]["statuses"]), "状态已生成")
    elif scenario == "gaussian":
        score = a["recovery"]["min_abs_correlation"]
        check("uncertainty", "高斯反例必须保留不可辨识/不确定解释",
              a["recovery"]["label"] == "uncertain_gaussian" or score < 0.90 or
              a["stability"]["min_match_to_selected"] < 0.90 or
              any(s["code"] == "near_gaussian" for s in a["interpretation"]["statuses"]),
              f"判定={a['recovery']['label']}, 恢复={score:.3f}, 稳定性={a['stability']['min_match_to_selected']:.3f}", critical=True)
    elif scenario == "rankdef":
        rank = d["effective_rank"]
        check("rank", "应诊断有效秩约为 2", rank <= 2, f"有效秩={rank}")
        check("rank_warning", "应说明不能识别全部来源",
              any(s["code"] == "rank_deficient" for s in a["interpretation"]["statuses"]), "状态已生成")
    elif scenario == "constant":
        check("constant", "应诊断常量通道", len(d["constant_channels"]) >= 1,
              d["constant_channels"])
    elif scenario == "missing":
        check("missing", "应诊断缺测区", d["total_missing_cells"] > 0,
              f"缺测单元={d['total_missing_cells']}")
        check("complete_samples", "应保留足够完整样本", d["train_complete_samples"] >= 32,
              d["train_complete_samples"], critical=False)
    elif scenario == "permutation":
        ev = a.get("evaluation") or {}
        naive = (ev.get("naive") or {}).get("metrics", {}).get("mean_r2")
        recal = (ev.get("recalibrated") or {}).get("metrics", {}).get("mean_r2")
        check("swap_mapping", "应生成显式通道映射", a.get("swap") and a["swap"].get("current_to_training"),
              (a.get("swap") or {}).get("current_to_training"))
        check("naive_fails", "不重标定应明显变差", naive is not None and recal is not None and naive < recal - 0.08,
              f"未重标定 R²={naive:.3f}, 重标定 R²={recal:.3f}", critical=False)
        check("recal_recovers", "显式重标定应恢复重建", recal is not None and recal >= 0.86,
              f"重标定 R²={recal:.3f}")
    return checks
