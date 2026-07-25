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


def _constrained_system(ds, constraints):
    """Base polytope plus the user's physical-unit constraints as extra rows."""
    A, b = ds.A, ds.b
    rows, bnds = build_constraints(ds, constraints or [])
    if rows:
        A = np.vstack([A, np.array(rows)])
        b = np.concatenate([b, np.array(bnds)])
    return A, b


# (i, j) -> unconstrained shadow result (the polytope is static)
_SHADOW_CACHE: dict[tuple[int, int], dict] = {}
_SHADOW_PAIRS_CACHE: list | None = None
_FLEX_BASE_CACHE: dict | None = None


def reset_caches() -> None:
    """Clear every polytope-derived cache. Call this whenever the active dataset
    changes (upload / reset) — all caches assume a single static polytope and are
    keyed globally, not per dataset."""
    global _SHADOW_PAIRS_CACHE, _FLEX_BASE_CACHE
    _SHADOW_CACHE.clear()
    _SHADOW_PAIRS_CACHE = None
    _FLEX_BASE_CACHE = None
    _DEPENDENCE_CACHE.clear()
    _EXTREMES_CACHE.clear()


def shadow(ds, x_axis: str, y_axis: str, constraints=None, n_dirs: int = 72):
    """Exact 2-D shadow (orthogonal projection) of the (optionally constrained)
    polytope onto two axes, via support-function LPs: for each direction theta,
    maximize cos(t)*x_i + sin(t)*x_j over the polytope; the maximizers' (x_i, x_j)
    trace the shadow polygon. Returned in physical units."""
    if x_axis not in ds.axes or y_axis not in ds.axes:
        raise KeyError(f"unknown axis {x_axis!r}/{y_axis!r}")
    i, j = ds.axes.index(x_axis), ds.axes.index(y_axis)
    cons = constraints or []
    key = (i, j) if not cons else None
    if key is not None and key in _SHADOW_CACHE:
        return _SHADOW_CACHE[key]

    A, b = _constrained_system(ds, cons)
    n = len(ds.axes)
    bounds = [(None, None)] * n
    pts = []
    for th in np.linspace(0.0, 2.0 * np.pi, n_dirs, endpoint=False):
        c = np.zeros(n)
        c[i] = -np.cos(th)
        c[j] = -np.sin(th)
        res = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method="highs")
        if res.success:
            pts.append((float(res.x[i]), float(res.x[j])))

    opt_phys = [
        float(ds.optimum_norm[i] * ds._scale[i] + ds._offset[i]),
        float(ds.optimum_norm[j] * ds._scale[j] + ds._offset[j]),
    ]
    base = {"x": x_axis, "y": y_axis, "optimum": opt_phys}

    if not pts:
        result = {**base, "feasible": False, "polygon": [], "boxiness": None}
        return result

    P = np.unique(np.round(np.array(pts), 9), axis=0)
    if P.shape[0] >= 3:
        try:
            from scipy.spatial import ConvexHull, QhullError
            hull = ConvexHull(P)
            poly = P[hull.vertices]
            area = float(hull.volume)  # 2-D: volume == area
        except QhullError:
            # collinear: order along the dominant direction -> segment
            d = P - P.mean(0)
            order = np.argsort(d @ d.std(0))
            poly = P[order][[0, -1]]
            area = 0.0
    else:
        poly = P
        area = 0.0

    w = float(P[:, 0].max() - P[:, 0].min())
    h = float(P[:, 1].max() - P[:, 1].min())
    boxiness = float(area / (w * h)) if w > 0 and h > 0 else 0.0
    poly_phys = [
        [float(p[0] * ds._scale[i] + ds._offset[i]), float(p[1] * ds._scale[j] + ds._offset[j])]
        for p in poly
    ]
    result = {**base, "feasible": True, "polygon": poly_phys, "boxiness": boxiness}
    if key is not None:
        _SHADOW_CACHE[key] = result
    return result


def shadow_pairs(ds):
    """All 45 axis pairs ranked by 'interestingness' (1 - boxiness of the exact
    shadow). Expensive on first call (~45x72 LPs), cached afterwards."""
    global _SHADOW_PAIRS_CACHE
    if _SHADOW_PAIRS_CACHE is not None:
        return _SHADOW_PAIRS_CACHE
    out = []
    for i in range(len(ds.axes)):
        for j in range(i + 1, len(ds.axes)):
            s = shadow(ds, ds.axes[i], ds.axes[j])
            out.append({"x": ds.axes[i], "y": ds.axes[j], "boxiness": s["boxiness"]})
    out.sort(key=lambda e: e["boxiness"] if e["boxiness"] is not None else 1.0)
    _SHADOW_PAIRS_CACHE = out
    return out


# sampler -> dependence matrices (computed once per sampler on a subsample)
_DEPENDENCE_CACHE: dict[str, dict] = {}
_DEPENDENCE_N = 1500   # subsample size: distance correlation is O(n^2) in memory


def _double_centered(x: np.ndarray) -> np.ndarray:
    """Double-centered pairwise-distance matrix of a 1-D variable (for dCov)."""
    D = np.abs(x[:, None] - x[None, :])
    return D - D.mean(0, keepdims=True) - D.mean(1, keepdims=True) + D.mean()


def dependence(ds, sampler: str, n: int = _DEPENDENCE_N) -> dict:
    """Pairwise statistical dependence between the 10 axes, three ways:

    - **distance correlation** (dcor): 0 iff independent, [0,1], catches nonlinear /
      non-monotonic coupling. The statistical analogue of the geometric facet
      'boxiness' — what actually trades off in the uniform near-optimal cloud.
    - **mutual information** (mi): k-NN (KSG-style) estimate, no binning; raw nats.
    - **pearson**: the linear baseline (near-useless here — included for contrast).

    All three are affine-invariant per axis, so normalized vs physical units give
    identical results; computed on a deterministic subsample (dCor is O(n^2)).
    Cached per sampler (the samples are static)."""
    if sampler in _DEPENDENCE_CACHE:
        return _DEPENDENCE_CACHE[sampler]

    X_full = ds.get_samples(sampler, "norm")          # (N, 10)
    N, d = X_full.shape
    rng = np.random.default_rng(42)
    idx = np.sort(rng.choice(N, size=min(n, N), replace=False))
    X = X_full[idx]                                   # (n, 10)

    # ---- distance correlation: one double-centered matrix per axis, then every
    # pair is a cheap elementwise-product mean (dCov^2). ----
    A = [_double_centered(X[:, k]) for k in range(d)]
    dvar2 = np.array([float((A[k] * A[k]).mean()) for k in range(d)])
    dcor = np.eye(d)
    for i in range(d):
        for j in range(i + 1, d):
            dcov2 = float((A[i] * A[j]).mean())
            denom = np.sqrt(dvar2[i] * dvar2[j])
            v = np.sqrt(max(dcov2, 0.0) / denom) if denom > 0 else 0.0
            dcor[i, j] = dcor[j, i] = v

    # ---- mutual information: k-NN estimator, per target column ----
    from sklearn.feature_selection import mutual_info_regression
    mi = np.zeros((d, d))
    for k in range(d):
        col = mutual_info_regression(X, X[:, k], discrete_features=False,
                                     n_neighbors=3, random_state=42)
        mi[k] = col
    mi = (mi + mi.T) / 2.0           # symmetrize the estimator
    np.fill_diagonal(mi, 0.0)        # self-MI (entropy) is not comparable; drop it

    # ---- pearson (linear baseline) ----
    pearson = np.corrcoef(X, rowvar=False)

    result = {
        "sampler": sampler,
        "axes": ds.axes,
        "n": int(X.shape[0]),
        "dcor": dcor.tolist(),
        "mi": mi.tolist(),
        "pearson": pearson.tolist(),
    }
    _DEPENDENCE_CACHE[sampler] = result
    return result


def flexibility(ds, constraints=None):
    """Exact remaining feasible [min, max] of every axis under the user's
    constraints (two LPs per axis), in physical units. The MGA 'how far can each
    lever still go' question, answered on the polytope rather than the sample."""
    global _FLEX_BASE_CACHE
    cons = constraints or []
    if not cons and _FLEX_BASE_CACHE is not None:
        return _FLEX_BASE_CACHE

    A, b = _constrained_system(ds, cons)
    # Feasibility only requires non-emptiness (the Chebyshev LP is infeasible iff
    # {Ax<=b} is empty). A zero radius just means no interior — e.g. an equality
    # pin min==max — which is perfectly valid here, unlike in generate() where
    # hit-and-run genuinely needs an interior point.
    x0, _radius = chebyshev_center(A, b)
    if x0 is None:
        return {"feasible": False, "ranges": []}

    n = len(ds.axes)
    bounds = [(None, None)] * n
    ranges = []
    for k in range(n):
        lohi = []
        for sign in (1.0, -1.0):
            c = np.zeros(n)
            c[k] = sign
            res = linprog(c, A_ub=A, b_ub=b, bounds=bounds, method="highs")
            lohi.append(float(res.x[k]) if res.success else None)
        lo, hi = lohi
        s, o = float(ds._scale[k]), float(ds._offset[k])
        ranges.append({
            "axis": ds.axes[k],
            "min": lo * s + o if lo is not None else None,
            "max": hi * s + o if hi is not None else None,
        })
    result = {"feasible": True, "ranges": ranges}
    if not cons:
        _FLEX_BASE_CACHE = result
    return result


def volume_ratio(ds, constraints, seed: int = 42, pop: int = 4000,
                 p0: float = 0.1, max_levels: int = 12):
    """Relative volume of the constrained region vs the full near-optimal space:
    ``vol({Ax<=b} ∩ box) / vol({Ax<=b})`` — i.e. "how much of the design space
    survives these constraints".

    The uniform-sample fraction (computed client-side) answers this directly when
    the region is not too small, but lands *zero* points once the region drops
    below ~1/N of the volume — exactly the tail the user cares about. This is a
    **subset-simulation** (multilevel-splitting) estimator that stays accurate
    there, reusing the same hit-and-run sampler.

    The box is the extra halfspaces ``r·x <= β`` (from :func:`build_constraints`).
    Define a normalized violation ``h(x) = max_r (r·x - β_r)/scale_r``; the target
    region is ``{h <= 0}``. We lower an intermediate threshold ``h_ℓ`` level by
    level so each conditional probability is ≈ ``p0``, drawing hit-and-run samples
    restricted to ``{Ax<=b, r·x <= β_r + h_ℓ·scale_r}`` (still a polytope), and
    multiply the per-level conditionals. Reaching 1e-8 takes ~8 levels, not the
    ~1e8 samples a naive count would need.
    """
    rows, bnds = build_constraints(ds, constraints or [])
    if not rows:
        return {"feasible": True, "ratio": 1.0, "log10": 0.0, "levels": 0,
                "method": "trivial", "cv": 0.0}
    R = np.asarray(rows, dtype=float)          # (q, n) box halfspaces
    beta = np.asarray(bnds, dtype=float)       # (q,)
    A0, b0 = ds.A, ds.b
    n = A0.shape[1]
    bounds = [(None, None)] * n

    # non-empty target? (thin slabs have ~0 inscribed radius but are still feasible)
    x0t, _ = chebyshev_center(np.vstack([A0, R]), np.concatenate([b0, beta]))
    if x0t is None:
        return {"feasible": False, "ratio": 0.0, "log10": None, "levels": 0,
                "method": "empty", "cv": 0.0}

    # per-row violation scale = max positive excess of that row over the base body
    scale = np.empty(len(R))
    for k in range(len(R)):
        res = linprog(-R[k], A_ub=A0, b_ub=b0, bounds=bounds, method="highs")
        excess = (-float(res.fun) - beta[k]) if res.success else 1.0
        scale[k] = excess if excess > 1e-9 else 1.0

    def hvals(X):
        return ((X @ R.T - beta) / scale).max(axis=1)

    rng = np.random.default_rng(seed)
    # level 0: reuse the precomputed uniform cloud (cheap, already uniform on {Ax<=b})
    base = ds.get_samples(None, "norm")
    X = base[rng.choice(len(base), min(pop, len(base)), replace=False)]
    h = hvals(X)
    x0_base, _ = chebyshev_center(A0, b0)

    prob = 1.0
    levels = 0
    while True:
        frac = float((h <= 0.0).mean())
        if frac >= p0 or levels >= max_levels:
            prob *= max(frac, 1.0 / (pop * (levels + 1)))   # final direct estimate
            break
        ht = float(np.quantile(h, p0))          # threshold letting ≈ p0 through
        if ht <= 0.0:
            prob *= max(frac, 1.0 / pop)
            break
        prob *= p0
        levels += 1
        seeds = X[h <= ht]
        start = seeds[rng.integers(len(seeds))] if len(seeds) else x0_base
        Al = np.vstack([A0, R])
        bl = np.concatenate([b0, beta + ht * scale])
        X = hit_and_run(Al, bl, pop, start, seed=seed + levels)
        if len(X) == 0:                          # sampler stalled; stop conservatively
            break
        h = hvals(X)

    # subset-simulation CV (rough; ignores MCMC autocorrelation → optimistic):
    # δ² ≈ Σ_ℓ (1 - p0) / (p0 · pop). Report as a multiplicative band.
    cv = float(np.sqrt(levels * (1.0 - p0) / (p0 * pop))) if levels else \
        float(np.sqrt(max(1.0 - prob, 0.0) / (prob * pop))) if prob > 0 else 0.0
    return {
        "feasible": True,
        "ratio": float(prob),
        "log10": (float(np.log10(prob)) if prob > 0 else None),
        "levels": int(levels),
        "method": "direct" if levels == 0 else "subset_simulation",
        "cv": cv,
    }


# sampler -> list of extreme designs (computed once; the polytope is static)
_EXTREMES_CACHE: dict[str, list] = {}


def extremes(ds, sampler: str):
    """For every technology, the designs with its minimum / maximum value over the
    near-optimal polytope (one LP per direction) — the classic MGA extreme
    alternatives. Returns physical values + position in the base PCA space."""
    if sampler in _EXTREMES_CACHE:
        return _EXTREMES_CACHE[sampler]
    from sklearn.decomposition import PCA

    base = ds.get_samples(sampler, "norm")[:, ds.tech_idx]
    model = PCA(n_components=2, random_state=42).fit(base)

    n = len(ds.axes)
    bounds = [(None, None)] * n
    out = []
    for i in ds.tech_idx:
        for kind, sign in (("min", 1.0), ("max", -1.0)):
            c = np.zeros(n)
            c[i] = sign
            res = linprog(c, A_ub=ds.A, b_ub=ds.b, bounds=bounds, method="highs")
            if not res.success:
                continue
            x = res.x
            phys = x * ds._scale + ds._offset
            pt = model.transform(x[ds.tech_idx].reshape(1, -1))[0]
            out.append({
                "axis": ds.axes[i],
                "kind": kind,
                "values": phys.tolist(),
                "point": pt.astype(float).tolist(),
            })
    _EXTREMES_CACHE[sampler] = out
    return out


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
