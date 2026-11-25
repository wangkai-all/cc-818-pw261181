"""Small deterministic linear-algebra helpers with no third-party dependencies.

Matrices are plain lists of lists.  Time-by-sample data are usually stored as
channel/source rows: ``X[channel][sample]``.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

Matrix = list[list[float]]
Vector = list[float]


def zeros(rows: int, cols: int) -> Matrix:
    return [[0.0 for _ in range(cols)] for _ in range(rows)]


def identity(n: int) -> Matrix:
    out = zeros(n, n)
    for i in range(n):
        out[i][i] = 1.0
    return out


def transpose(a: Matrix) -> Matrix:
    if not a:
        return []
    return [list(row) for row in zip(*a)]


def matmul(a: Matrix, b: Matrix) -> Matrix:
    if not a or not b:
        return []
    n, k1 = len(a), len(a[0])
    k2, m = len(b), len(b[0])
    if k1 != k2:
        raise ValueError(f"incompatible shapes: ({n},{k1}) x ({k2},{m})")
    out = zeros(n, m)
    for i in range(n):
        ai = a[i]
        oi = out[i]
        for k in range(k1):
            aik = ai[k]
            if aik == 0.0:
                continue
            bk = b[k]
            for j in range(m):
                oi[j] += aik * bk[j]
    return out


def matvec(a: Matrix, x: Vector) -> Vector:
    return [sum(aij * xj for aij, xj in zip(row, x)) for row in a]


def add(a: Matrix, b: Matrix) -> Matrix:
    return [[x + y for x, y in zip(ra, rb)] for ra, rb in zip(a, b)]


def scale(a: Matrix, s: float) -> Matrix:
    return [[x * s for x in row] for row in a]


def column_dot(a: Matrix, i: int, b: Matrix, j: int) -> float:
    """Dot product of columns i and j of two row-oriented matrices."""
    return sum(a[r][i] * b[r][j] for r in range(len(a)))


def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def variance(xs: Sequence[float], mean_value: float | None = None) -> float:
    if not xs:
        return float("nan")
    mu = mean(xs) if mean_value is None else mean_value
    return sum((x - mu) ** 2 for x in xs) / len(xs)


def std(xs: Sequence[float]) -> float:
    return math.sqrt(max(0.0, variance(xs)))


def corrcoef(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson correlation; zero-variance series return 0."""
    n = min(len(x), len(y))
    if n == 0:
        return 0.0
    mx = sum(x[:n]) / n
    my = sum(y[:n]) / n
    sxx = syy = sxy = 0.0
    for i in range(n):
        dx = x[i] - mx
        dy = y[i] - my
        sxx += dx * dx
        syy += dy * dy
        sxy += dx * dy
    if sxx <= 1e-30 or syy <= 1e-30:
        return 0.0
    c = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, c))


def covariance_rows(x_rows: Matrix, indices: Sequence[int] | None = None) -> Matrix:
    """Covariance of row variables using population normalization."""
    p = len(x_rows)
    if p == 0:
        return []
    if indices is None:
        indices = range(len(x_rows[0]))
    idx = list(indices)
    n = len(idx)
    means = [sum(row[i] for i in idx) / n for row in x_rows]
    cov = zeros(p, p)
    for a in range(p):
        for b in range(a, p):
            value = 0.0
            ma, mb = means[a], means[b]
            for i in idx:
                value += (x_rows[a][i] - ma) * (x_rows[b][i] - mb)
            value /= max(1, n)
            cov[a][b] = cov[b][a] = value
    return cov


def eigh_jacobi(a: Matrix, tolerance: float = 1e-12, max_sweeps: int = 100) -> tuple[Vector, Matrix]:
    """Symmetric eigen-decomposition.

    Returns eigenvalues descending and eigenvectors as rows? No: returns
    ``(values, vectors)`` where ``vectors[r][k]`` is component r of eigenvector
    k.  Thus eigenvector k is ``[vectors[r][k] for r]``.
    """
    n = len(a)
    if n == 0:
        return [], []
    if any(len(row) != n for row in a):
        raise ValueError("matrix must be square")
    m = [row[:] for row in a]
    v = identity(n)

    for _ in range(max_sweeps):
        off = 0.0
        for i in range(n):
            for j in range(i + 1, n):
                off += abs(m[i][j])
        scale_ref = max(1.0, max(abs(m[i][i]) for i in range(n)))
        if off <= tolerance * scale_ref:
            break

        for p in range(n - 1):
            for q in range(p + 1, n):
                mpq = m[p][q]
                threshold = tolerance * scale_ref
                if abs(mpq) <= threshold:
                    m[p][q] = m[q][p] = 0.0
                    continue

                app = m[p][p]
                aqq = m[q][q]
                tau = (aqq - app) / (2.0 * mpq)
                if tau >= 0.0:
                    t = 1.0 / (tau + math.sqrt(1.0 + tau * tau))
                else:
                    t = -1.0 / (-tau + math.sqrt(1.0 + tau * tau))
                c = 1.0 / math.sqrt(1.0 + t * t)
                s = t * c

                # Rotate rows/columns p and q.
                for i in range(n):
                    if i == p or i == q:
                        continue
                    mip, miq = m[i][p], m[i][q]
                    nip = c * mip - s * miq
                    niq = s * mip + c * miq
                    m[i][p] = m[p][i] = nip
                    m[i][q] = m[q][i] = niq

                mpp = c * c * app - 2.0 * s * c * mpq + s * s * aqq
                mqq = s * s * app + 2.0 * s * c * mpq + c * c * aqq
                m[p][p] = mpp
                m[q][q] = mqq
                m[p][q] = m[q][p] = 0.0

                for i in range(n):
                    vip, viq = v[i][p], v[i][q]
                    v[i][p] = c * vip - s * viq
                    v[i][q] = s * vip + c * viq

    values = [m[i][i] for i in range(n)]
    order = sorted(range(n), key=lambda i: values[i], reverse=True)
    sorted_values = [max(0.0, values[i]) for i in order]
    sorted_vectors = [[v[r][i] for i in order] for r in range(n)]
    return sorted_values, sorted_vectors


def sym_sqrt_inv(a: Matrix) -> Matrix:
    """Inverse symmetric square root ``a^{-1/2}`` for positive symmetric A."""
    vals, vecs = eigh_jacobi(a)
    n = len(a)
    out = zeros(n, n)
    for r in range(n):
        for c in range(n):
            total = 0.0
            for k in range(n):
                lam = max(vals[k], 1e-14)
                total += vecs[r][k] * (lam ** -0.5) * vecs[c][k]
            out[r][c] = total
    return out


def symmetric_orthogonalize(w: Matrix) -> Matrix:
    """Return W transformed so W W^T = I."""
    wt = transpose(w)
    wwt = matmul(w, wt)
    power = sym_sqrt_inv(wwt)
    return matmul(power, w)


def pinv_full_row_rank(a: Matrix) -> Matrix:
    """Pseudoinverse for a full-row-rank matrix: A^T (A A^T)^-1."""
    if not a:
        return []
    gram = matmul(a, transpose(a))
    inv = sym_inv(gram)
    return matmul(transpose(a), inv)


def sym_inv(a: Matrix) -> Matrix:
    vals, vecs = eigh_jacobi(a)
    n = len(a)
    out = zeros(n, n)
    for r in range(n):
        for c in range(n):
            total = 0.0
            for k in range(n):
                lam = vals[k]
                if lam <= 1e-12 * max(vals[0], 1.0):
                    invlam = 0.0
                else:
                    invlam = 1.0 / lam
                total += vecs[r][k] * invlam * vecs[c][k]
            out[r][c] = total
    return out


def solve_linear_fit(x_rows: Matrix, y_rows: Matrix) -> Matrix:
    """Least-squares A minimizing ||X - A Y|| for row matrices.

    X and Y are variable-by-sample.  Returns A with shape len(X) x len(Y).
    """
    k = len(y_rows)
    # Normal equations use raw cross-products consistently.  Dividing only one
    # side by n would silently shrink the fitted mixing matrix by 1/n.
    yyt = matmul(y_rows, transpose(y_rows))
    inv = sym_inv(yyt)
    xty = matmul(x_rows, transpose(y_rows))
    return matmul(xty, inv)


def assignment_maximize(scores: Matrix) -> list[tuple[int, int]]:
    """Maximum-weight bipartite assignment using exhaustive search when small.

    The BSS lab only assigns at most a handful of components/sources, so a
    deterministic exhaustive assignment is simpler and more trustworthy than a
    large Hungarian implementation.  A greedy fallback is supplied above 8.
    Returns pairs ``(row, column)``; rectangular matrices are supported.
    """
    rows = len(scores)
    cols = len(scores[0]) if rows else 0
    if rows == 0 or cols == 0:
        return []

    if max(rows, cols) <= 8:
        if rows <= cols:
            best_score = -float("inf")
            best: list[tuple[int, int]] = []

            def visit(row: int, used: set[int], pairs: list[tuple[int, int]], total: float) -> None:
                nonlocal best_score, best
                if row == rows:
                    if total > best_score:
                        best_score, best = total, pairs[:]
                    return
                # Prune impossible weak branches only by ordering candidates.
                candidates = sorted(range(cols), key=lambda c: scores[row][c], reverse=True)
                for c in candidates:
                    if c not in used:
                        used.add(c)
                        pairs.append((row, c))
                        visit(row + 1, used, pairs, total + scores[row][c])
                        pairs.pop()
                        used.remove(c)

            visit(0, set(), [], 0.0)
            return best

        pairs_col_row = assignment_maximize([[scores[r][c] for r in range(rows)] for c in range(cols)])
        return [(r, c) for c, r in pairs_col_row]

    # Greedy fallback (not used by the default small lab dimensions).
    candidates = sorted(
        ((scores[r][c], r, c) for r in range(rows) for c in range(cols)),
        reverse=True,
    )
    used_r: set[int] = set()
    used_c: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, r, c in candidates:
        if r not in used_r and c not in used_c:
            pairs.append((r, c))
            used_r.add(r)
            used_c.add(c)
    return sorted(pairs)


def match_series(estimated: Matrix, reference: Matrix) -> list[dict]:
    """Match every estimated row to a distinct reference row by |corr|."""
    q = len(estimated)
    k = len(reference)
    scores_abs = zeros(q, k)
    signed = zeros(q, k)
    for i in range(q):
        for j in range(k):
            c = corrcoef(estimated[i], reference[j])
            signed[i][j] = c
            scores_abs[i][j] = abs(c)
    pairs = assignment_maximize(scores_abs)
    by_est = {i: j for i, j in pairs}
    result = []
    for i in range(q):
        j = by_est.get(i)
        c = signed[i][j] if j is not None else 0.0
        result.append(
            {
                "estimated_index": i,
                "reference_index": j,
                "correlation": c,
                "abs_correlation": abs(c),
                "sign": 1 if c >= 0 else -1,
            }
        )
    return result


def aligned_copy(estimated: Matrix, matches: Sequence[dict], reference_count: int) -> Matrix:
    """Reorder/flip estimated rows into reference order where possible."""
    out: Matrix = [[] for _ in range(reference_count)]
    for m in matches:
        j = m["reference_index"]
        if j is None or j >= reference_count:
            continue
        sign = m.get("sign", 1)
        out[j] = [sign * x for x in estimated[m["estimated_index"]]]
    return out


def kurtosis_excess(x: Sequence[float]) -> float:
    n = len(x)
    if n < 8:
        return 0.0
    mu = sum(x) / n
    m2 = sum((v - mu) ** 2 for v in x) / n
    if m2 <= 1e-30:
        return 0.0
    m4 = sum((v - mu) ** 4 for v in x) / n
    return m4 / (m2 * m2) - 3.0


def mutual_information(x: Sequence[float], y: Sequence[float], bins: int = 10) -> float:
    """Binned normalized mutual information in [0, 1]."""
    n = min(len(x), len(y))
    if n < bins * 3:
        return 0.0
    x = x[:n]
    y = y[:n]

    def quantile_edges(v: Sequence[float]) -> Vector:
        sv = sorted(v)
        return [sv[min(n - 1, int((b / bins) * n))] for b in range(1, bins)]

    ex = quantile_edges(x)
    ey = quantile_edges(y)

    def bin_index(v: float, edges: Vector) -> int:
        lo, hi = 0, len(edges)
        while lo < hi:
            mid = (lo + hi) // 2
            if v <= edges[mid]:
                hi = mid
            else:
                lo = mid + 1
        return min(bins - 1, lo)

    joint = [[0 for _ in range(bins)] for _ in range(bins)]
    cx = [0] * bins
    cy = [0] * bins
    for xv, yv in zip(x, y):
        i = bin_index(xv, ex)
        j = bin_index(yv, ey)
        joint[i][j] += 1
        cx[i] += 1
        cy[j] += 1

    mi = 0.0
    hx = hy = 0.0
    for i in range(bins):
        px = cx[i] / n
        if px > 0:
            hx -= px * math.log(px)
    for j in range(bins):
        py = cy[j] / n
        if py > 0:
            hy -= py * math.log(py)
    for i in range(bins):
        for j in range(bins):
            c = joint[i][j]
            if c:
                pxy = c / n
                px = cx[i] / n
                py = cy[j] / n
                mi += pxy * math.log(pxy / (px * py))
    denom = min(hx, hy)
    return max(0.0, mi / denom) if denom > 1e-12 else 0.0
