"""Deterministic local synthetic mixtures.

No audio files, instruments, network resources, or third-party packages are
used.  The generator deliberately retains ground-truth sources and mixing
coefficients so the UI can audit whether a decomposition merely reconstructs
the sensors or actually recovers the generating components.
"""
from __future__ import annotations

import math
import random
from typing import Any

from .linalg import Matrix, std

DEFAULT_N = 1600
DEFAULT_FS = 100.0

CHANNEL_UNITS = ["mV", "mA", "µε", "g", "°C", "a.u."]
CHANNEL_GAINS = [1.35, 0.62, 2.10, 0.88, 1.55, 0.73]
CHANNEL_OFFSETS = [0.18, -0.11, 0.06, -0.04, 0.12, -0.08]


def _standardize(values: list[float]) -> list[float]:
    mu = sum(values) / len(values)
    sd = std(values)
    if sd < 1e-12:
        return [0.0 for _ in values]
    return [(v - mu) / sd for v in values]


def _source_waveform(kind: str, n: int, fs: float, rng: random.Random, index: int) -> list[float]:
    out: list[float] = []
    phase = rng.random()
    for i in range(n):
        t = i / fs
        if kind == "square":
            f = 0.85 + 0.23 * index
            out.append(1.0 if math.sin(2.0 * math.pi * f * t + phase) >= 0 else -1.0)
        elif kind == "sawtooth":
            f = 0.53 + 0.17 * index
            x = (f * t + phase * 0.17) % 1.0
            out.append(2.0 * x - 1.0)
        elif kind == "chirp":
            f0 = 0.35 + 0.08 * index
            f1 = 2.2 + 0.3 * index
            phase_v = 2.0 * math.pi * (f0 * t + 0.5 * (f1 - f0) / (n / fs) * t * t)
            out.append(math.sin(phase_v))
        elif kind == "sparse":
            # Sparse impulses with exponential tails.  A fresh deterministic
            # random draw is made at every sample; Bernoulli events provide
            # high excess kurtosis.
            event = rng.random() < 0.035
            if event:
                out.append(rng.choice([-1.0, 1.0]) * rng.uniform(1.4, 2.8))
            elif out:
                out.append(0.72 * out[-1] + rng.gauss(0.0, 0.025))
            else:
                out.append(rng.gauss(0.0, 0.025))
        elif kind == "gaussian":
            out.append(rng.gauss(0.0, 1.0))
        elif kind == "am":
            carrier = math.sin(2.0 * math.pi * (2.1 + 0.3 * index) * t + phase)
            envelope = 0.55 + 0.45 * math.sin(2.0 * math.pi * (0.18 + 0.04 * index) * t)
            out.append(envelope * carrier)
        else:  # pragma: no cover - defensive default
            out.append(math.sin(2.0 * math.pi * (0.7 + 0.2 * index) * t))
    return _standardize(out)


def _mixing_matrix(m: int, k: int, rng: random.Random, rank: int | None = None) -> Matrix:
    if rank is None or rank >= k:
        a = [[rng.uniform(-1.0, 1.0) for _ in range(k)] for _ in range(m)]
    else:
        r = max(1, min(rank, k, m))
        left = [[rng.uniform(-1.0, 1.0) for _ in range(r)] for _ in range(m)]
        coeff = [[rng.uniform(-0.9, 0.9) for _ in range(k)] for _ in range(r)]
        a = [[sum(left[i][h] * coeff[h][j] for h in range(r)) for j in range(k)] for i in range(m)]
    # Avoid nearly zero rows and make signs reproducibly varied.
    for i in range(m):
        if all(abs(v) < 0.15 for v in a[i]):
            a[i][i % k] += 0.8 * (1 if i % 2 == 0 else -1)
    return a


def injection_waveform(kind: str, n: int, fs: float, rng: random.Random) -> list[float]:
    if kind == "chirp":
        return _standardize(_source_waveform("chirp", n, fs, rng, 7))
    if kind == "square":
        return _standardize(_source_waveform("square", n, fs, rng, 9))
    # Periodic smooth pulses, clearly known and easy to match.
    values = []
    period = int(0.9 * fs)
    width = max(3, int(0.055 * fs))
    for i in range(n):
        d = min(i % period, period - i % period)
        values.append(math.exp(-0.5 * (d / width) ** 2) * (1.0 if (i // period) % 2 == 0 else -0.85))
    return _standardize(values)


def injection_vector(style: str, m: int) -> list[float]:
    if style == "opposed":
        base = [1.0, -0.82, 0.63, -0.48, 0.34, -0.25]
    elif style == "gradient":
        base = [0.35 + 0.65 * i / max(1, m - 1) for i in range(6)]
    else:
        base = [1.0, 0.88, 0.94, 0.76, 0.82, 0.70]
    return [base[i % len(base)] for i in range(m)]


def generate_dataset(config: dict[str, Any]) -> dict[str, Any]:
    scenario = config.get("scenario", "independent")
    seed = int(config.get("seed", 2611818))
    n = int(config.get("n_samples", DEFAULT_N))
    fs = float(config.get("sample_rate", DEFAULT_FS))
    m = int(config.get("n_channels", 4))
    k = int(config.get("n_sources", 3))
    noise_sd = float(config.get("noise", 0.018))
    rng = random.Random(seed)

    # Scenario defaults do not override explicit structural choices except the
    # special counterexamples.
    if scenario == "rankdef":
        rank = 2
    else:
        rank = None

    kind_sets = {
        "independent": ["square", "sawtooth", "sparse", "chirp", "am"],
        "correlated": ["square", "sawtooth", "sparse", "chirp", "am"],
        "gaussian": ["gaussian", "gaussian", "sparse", "chirp", "sawtooth"],
        "rankdef": ["square", "sawtooth", "sparse", "chirp"],
        "constant": ["square", "sawtooth", "sparse", "chirp"],
        "missing": ["square", "sawtooth", "sparse", "chirp"],
        "permutation": ["square", "sawtooth", "sparse", "chirp"],
    }
    kinds = kind_sets.get(scenario, kind_sets["independent"])[:k]
    while len(kinds) < k:
        kinds.append(["square", "sawtooth", "sparse", "chirp", "am"][len(kinds) % 5])

    source_rows: Matrix = []
    source_meta: list[dict[str, Any]] = []
    for j, kind in enumerate(kinds):
        row = _source_waveform(kind, n, fs, rng, j)
        source_rows.append(row)
        source_meta.append(
            {
                "id": f"s{j}",
                "name": f"真实来源 {j + 1}",
                "kind": kind,
                "physical_name": None,
                "values": row,
            }
        )

    if scenario == "correlated" and k >= 2:
        rho = 0.78
        a = source_rows[0]
        b = source_rows[1]
        mixed = [rho * x + math.sqrt(max(0.0, 1 - rho * rho)) * y for x, y in zip(a, b)]
        source_rows[1] = _standardize(mixed)
        source_meta[1]["kind"] = "correlated_with_source_1"
        source_meta[1]["correlation_to_s0"] = rho
        source_meta[1]["values"] = source_rows[1]

    a_base = _mixing_matrix(m, k, rng, rank=rank)
    gains = CHANNEL_GAINS[:m]
    offsets = CHANNEL_OFFSETS[:m]
    units = CHANNEL_UNITS[:m]
    a_true = [[gains[i] * a_base[i][j] for j in range(k)] for i in range(m)]

    injection = config.get("injection") or {}
    injection_enabled = bool(injection.get("enabled", False))
    injection_info: dict[str, Any] | None = None
    if injection_enabled:
        inj_rng = random.Random(seed + 9173)
        inj_kind = injection.get("kind", "pulse")
        s_inj = injection_waveform(inj_kind, n, fs, inj_rng)
        spatial = injection_vector(injection.get("spatial", "uniform"), m)
        level = float(injection.get("amplitude", 0.9))
        inj_col = [gains[i] * level * spatial[i] for i in range(m)]
        for i in range(m):
            a_true[i].append(inj_col[i])
        source_rows.append(s_inj)
        source_meta.append(
            {
                "id": "injected",
                "name": "已知注入来源",
                "kind": f"known_{inj_kind}",
                "physical_name": None,
                "values": s_inj,
            }
        )
        injection_info = {
            "kind": inj_kind,
            "amplitude": level,
            "spatial": injection.get("spatial", "uniform"),
            "unit_vector": spatial,
            "mixing_column_raw": inj_col,
            "seed": seed + 9173,
        }

    x: Matrix = [[0.0 for _ in range(n)] for _ in range(m)]
    for i in range(m):
        for t in range(n):
            signal = sum(a_true[i][j] * source_rows[j][t] for j in range(len(source_rows)))
            x[i][t] = offsets[i] + signal + rng.gauss(0.0, noise_sd * gains[i])

    constant_channel = None
    if scenario == "constant":
        constant_channel = min(2, m - 1)
        x[constant_channel] = [offsets[constant_channel] for _ in range(n)]

    missing_enabled = bool(config.get("missing_enabled", scenario == "missing"))
    missing_mask = [[False for _ in range(n)] for _ in range(m)]
    missing_ranges_config = config.get("missing_ranges")
    if missing_enabled:
        if missing_ranges_config:
            ranges = missing_ranges_config
        else:
            ranges = [
                {"channel": 0, "start": int(0.20 * n), "end": int(0.255 * n)},
                {"channel": min(2, m - 1), "start": int(0.61 * n), "end": int(0.675 * n)},
            ]
        for spec in ranges:
            ch = int(spec["channel"])
            if 0 <= ch < m:
                lo = max(0, int(spec["start"]))
                hi = min(n, int(spec["end"]))
                for t in range(lo, hi):
                    missing_mask[ch][t] = True
                    x[ch][t] = None  # type: ignore[assignment]

    channels = []
    for i in range(m):
        values = [None if missing_mask[i][t] else round(float(x[i][t]), 7) for t in range(n)]
        channels.append(
            {
                "id": f"ch{i}",
                "name": f"传感器 {i + 1}",
                "unit": units[i],
                "gain": gains[i],
                "offset": offsets[i],
                "values": values,
            }
        )

    return {
        "scenario": scenario,
        "seed": seed,
        "n_samples": n,
        "sample_rate": fs,
        "time": [round(i / fs, 4) for i in range(n)],
        "channels": channels,
        "true_sources": source_meta,
        "mixing_true": a_true,
        "mixing_base": a_base,
        "channel_gain": gains,
        "channel_offset": offsets,
        "channel_unit": units,
        "noise_sd": noise_sd,
        "missing_mask": missing_mask,
        "constant_channel": constant_channel,
        "injection": injection_info,
        "known_structure": {
            "true_source_count": len(source_meta),
            "true_mixing_rank": rank if rank is not None else min(m, len(source_meta)),
            "sources_are_independent": scenario not in {"correlated"},
            "near_gaussian_sources": 2 if scenario == "gaussian" else 0,
        },
    }
