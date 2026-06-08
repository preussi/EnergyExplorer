"""Constraint-based candidate generation: add user constraints to the polytope
and draw fresh uniform samples from the constrained near-optimal region.

Self-contained (numpy + scipy) so there is no native sampler build dependency.
Sampling is uniform hit-and-run; feasibility / a starting interior point come
from the Chebyshev center LP.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import linprog


def chebyshev_center(A: np.ndarray, b: np.ndarray):
    """Deepest interior point of {x : A x <= b} and its inscribed-ball radius.
    Returns (x, r); r <= 0 (or None) means the polytope has no interior."""
    m, n = A.shape
    norms = np.linalg.norm(A, axis=1)
    # maximize r s.t. a_i . x + ||a_i|| r <= b_i, r >= 0
    A_ub = np.hstack([A, norms.reshape(-1, 1)])
    c = np.zeros(n + 1)
    c[-1] = -1.0
    bounds = [(None, None)] * n + [(0.0, None)]
    res = linprog(c, A_ub=A_ub, b_ub=b, bounds=bounds, method="highs")
    if not res.success:
        return None, 0.0
    return res.x[:n], float(res.x[n])


def hit_and_run(
    A: np.ndarray, b: np.ndarray, n_samples: int, x0: np.ndarray,
    seed: int = 42, burn: int = 250, thin: int = 8,
) -> np.ndarray:
    """Uniform hit-and-run over {x : A x <= b}, starting from interior point x0."""
    rng = np.random.default_rng(seed)
    n = A.shape[1]
    x = np.asarray(x0, dtype=float).copy()
    out = np.empty((n_samples, n))
    got = 0
    k = 0
    max_iter = burn + n_samples * thin + 2000
    while got < n_samples and k < max_iter:
        k += 1
        d = rng.standard_normal(n)
        d /= np.linalg.norm(d) + 1e-12
        Ad = A @ d
        resid = np.maximum(b - A @ x, 0.0)  # distance to each face (>= 0 inside)
        tmax, tmin = np.inf, -np.inf
        pos = Ad > 1e-12
        neg = Ad < -1e-12
        if pos.any():
            tmax = float(np.min(resid[pos] / Ad[pos]))
        if neg.any():
            tmin = float(np.max(resid[neg] / Ad[neg]))
        if not (tmax - tmin > 1e-12):
            continue  # degenerate direction; skip
        x = x + rng.uniform(tmin, tmax) * d
        if k > burn and (k - burn) % thin == 0:
            out[got] = x
            got += 1
    return out[:got]


def build_constraints(ds, constraints):
    """Translate physical-unit {axis, min, max} constraints into extra rows of the
    normalized polytope. A linear bound phys_i = s_i*norm_i + o_i <= hi becomes the
    row (s_i * e_i) . norm <= hi - o_i (sign handled by keeping s_i in the row)."""
    rows, bnds = [], []
    n = len(ds.axes)
    for c in constraints:
        axis = c.get("axis")
        if axis not in ds.axes:
            continue
        i = ds.axes.index(axis)
        s = float(ds._scale[i])
        o = float(ds._offset[i])
        hi = c.get("max", None)
        lo = c.get("min", None)
        if hi is not None:
            r = np.zeros(n); r[i] = s
            rows.append(r); bnds.append(hi - o)
        if lo is not None:
            r = np.zeros(n); r[i] = -s
            rows.append(r); bnds.append(-(lo - o))
    return rows, bnds


def generate(ds, sampler: str, n: int, constraints, seed: int = 42):
    """Return dict with feasibility + generated candidates (phys values) + their
    PCA projection in the base sampler's PCA space."""
    from sklearn.decomposition import PCA

    A = ds.A.copy()
    b = ds.b.copy()
    rows, bnds = build_constraints(ds, constraints)
    if rows:
        A = np.vstack([A, np.array(rows)])
        b = np.concatenate([b, np.array(bnds)])

    x0, radius = chebyshev_center(A, b)
    if x0 is None or radius < 1e-7:
        return {"feasible": False, "n": 0, "points": [], "values": [],
                "fields": ds.axes, "radius": float(radius)}

    n = max(1, min(int(n), 5000))
    samp = hit_and_run(A, b, n, x0, seed=seed)
    phys = samp * ds._scale + ds._offset

    base = ds.get_samples(sampler, "norm")[:, ds.tech_idx]
    model = PCA(n_components=2, random_state=42).fit(base)
    pts = model.transform(samp[:, ds.tech_idx])

    return {
        "feasible": True,
        "n": int(samp.shape[0]),
        "points": pts.astype(float).tolist(),
        "values": phys.astype(float).tolist(),
        "fields": ds.axes,
        "radius": float(radius),
    }
