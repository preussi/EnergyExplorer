"""Constraint-based candidate generation: add user constraints to the polytope
and draw fresh uniform samples from the constrained near-optimal region.

Self-contained (numpy + scipy) so there is no native sampler build dependency.
Sampling is uniform hit-and-run; feasibility / a starting interior point come
from the Chebyshev center LP.
"""
from __future__ import annotations

import time

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


# Every cache below is keyed to *its* dataset (`ds.cache`), not to module state:
# each user's session holds a different polytope, and a global cache would serve
# one user's LP results to another. A fresh Dataset starts with empty caches, so
# nothing ever needs invalidating.
def _slot(ds, key: str, default):
    """Get-or-create this dataset's cache bucket for `key`."""
    return ds.cache.setdefault(key, default)


# ---- facet shape taxonomy -------------------------------------------------
# A facet is an orthogonal projection of a convex body, so it is ALWAYS convex:
# no holes, no reflex vertices. Its bounding box is tight, so it touches all four
# sides. The only structural freedom left is therefore *which corners of the box
# it cannot reach* — which makes this classification complete rather than ad hoc.
#
# Normalizing by the facet's own bounding box makes the measure scale-free, so a
# GW x GW pair and a GW x ktCO2/h pair are directly comparable.
#
#   g_UR > 0  can't both be HIGH        -> trade-off
#   g_LL > 0  can't both be LOW         -> at least one is required
#   g_LR > 0  x high with y low is out  -> x REQUIRES y   (directional!)
#   g_UL > 0  y high with x low is out  -> y REQUIRES x
#
# The diagonals say different things: {LL,UR} constrains the sum, {UL,LR} the
# difference.
CORNERS = {"LL": (0.0, 0.0), "LR": (1.0, 0.0), "UL": (0.0, 1.0), "UR": (1.0, 1.0)}
# Below this a cut corner is not visually distinguishable from a box. Calibrated
# on polytope_13: the near-box pairs top out at 0.11, the trade-offs start at 0.25.
GAP_EPS = 0.15


def _cheb_to_segment(p, q, c) -> float:
    """min over the segment p->q of the Chebyshev distance to point c.

    Convex in the segment parameter, so ternary search converges to machine
    precision in a fixed number of steps (no sampling artefacts)."""
    def f(t: float) -> float:
        return max(abs(p[0] + (q[0] - p[0]) * t - c[0]),
                   abs(p[1] + (q[1] - p[1]) * t - c[1]))
    lo, hi = 0.0, 1.0
    for _ in range(80):
        m1, m2 = lo + (hi - lo) / 3.0, hi - (hi - lo) / 3.0
        if f(m1) < f(m2):
            hi = m2
        else:
            lo = m1
    return float(f((lo + hi) / 2.0))


def _inside(pt, poly) -> bool:
    """Ray-cast point-in-polygon (the polygon is convex and closed)."""
    x, y = pt
    inside = False
    n = len(poly)
    for k in range(n):
        x1, y1 = poly[k]
        x2, y2 = poly[(k + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def corner_gaps(poly) -> dict[str, float] | None:
    """Chebyshev gap from each bounding-box corner to the facet, on the facet
    normalized into the unit square. 0 = that corner is attainable."""
    if poly is None or len(poly) < 3:
        return None
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    N = [((p[0] - x0) / (x1 - x0), (p[1] - y0) / (y1 - y0)) for p in poly]
    out = {}
    for name, c in CORNERS.items():
        if _inside(c, N):
            out[name] = 0.0
        else:
            out[name] = min(_cheb_to_segment(N[k], N[(k + 1) % len(N)], c)
                            for k in range(len(N)))
    return out


def classify_facet(gaps, x_axis: str, y_axis: str) -> dict:
    """Label a facet from its corner gaps. `band` and `locked` do not occur in
    polytope_13 (epsilon=0.1 is generous) but are detected so a tighter or
    uploaded polytope isn't mislabelled as one of the simple cases."""
    if not gaps:
        return {"category": "unknown", "strength": 0.0, "cut": [], "detail": ""}
    hot = {k: v for k, v in gaps.items() if v >= GAP_EPS}
    strength = max(hot.values()) if hot else 0.0
    cut = sorted(hot, key=lambda k: -hot[k])
    sx, sy = x_axis, y_axis
    if not hot:
        return {"category": "independent", "strength": 0.0, "cut": [],
                "detail": f"{sx} and {sy} can be chosen independently"}
    if "LL" in hot and "UR" in hot:
        return {"category": "band", "strength": strength, "cut": cut,
                "detail": f"{sx} and {sy} substitute one-for-one — their total is pinned"}
    if "LR" in hot and "UL" in hot:
        return {"category": "locked", "strength": strength, "cut": cut,
                "detail": f"{sx} and {sy} have to move together"}
    top = cut[0]
    if top == "UR":
        return {"category": "tradeoff", "strength": strength, "cut": cut,
                "detail": f"{sx} and {sy} cannot both be large"}
    if top == "LL":
        return {"category": "at_least_one", "strength": strength, "cut": cut,
                "detail": f"{sx} and {sy} cannot both be small — at least one is required"}
    # dependency is DIRECTIONAL: which one needs the other is the useful part
    needs, needed = (sx, sy) if top == "LR" else (sy, sx)
    return {"category": "dependency", "strength": strength, "cut": cut,
            "needs": needs, "needed": needed,
            "detail": f"{needs} at scale requires {needed}"}


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
    shadows = _slot(ds, "shadow", {})   # (i, j) -> unconstrained shadow result
    if key is not None and key in shadows:
        return shadows[key]

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
    gaps = corner_gaps(poly_phys)
    shape = classify_facet(gaps, x_axis, y_axis)
    result = {**base, "feasible": True, "polygon": poly_phys, "boxiness": boxiness,
              "corner_gaps": gaps, "shape": shape}
    if key is not None:
        shadows[key] = result
    return result


def shadow_pairs(ds, budget_s: float = 25.0):
    """Every axis pair (C(n,2)) with its exact shadow: boxiness, corner gaps, shape
    and the polygon itself.

    The polygon rides along so the matrix can draw all its mini-outlines from ONE
    response. It used to fetch them per pair — 36 requests at 9 axes, but 276 at the
    24-axis cap, which is a request storm for data we already have here.

    **Bounded.** This is the quadratic term of a build: 36 pairs ≈ 2.4 s, but 276
    ≈ 47 s. Pairs are computed most-coupled-first (so the interesting ones are
    ready) until `budget_s` is spent; the rest come back `pending` and are filled in
    on demand by `/api/shadow`, which caches into the same slot.
    """
    cached = ds.cache.get("shadow_pairs")
    if cached is not None:
        return cached

    n = len(ds.axes)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    # most-coupled first, when dependence is already warm (it is: _warm runs it first)
    dep = ds.cache.get("dependence", {}).get("chrrt")
    if dep and dep.get("dcor"):
        D = dep["dcor"]
        pairs.sort(key=lambda ij: -D[ij[0]][ij[1]])

    out, t0, spent = [], time.perf_counter(), False
    for i, j in pairs:
        x, y = ds.axes[i], ds.axes[j]
        if spent:
            out.append({"x": x, "y": y, "boxiness": None, "corner_gaps": None,
                        "shape": None, "polygon": None, "pending": True})
            continue
        s_ = shadow(ds, x, y)
        out.append({"x": x, "y": y, "boxiness": s_["boxiness"],
                    "corner_gaps": s_.get("corner_gaps"), "shape": s_.get("shape"),
                    "polygon": s_.get("polygon"), "pending": False})
        if time.perf_counter() - t0 > budget_s:
            spent = True
    out.sort(key=lambda e: e["boxiness"] if e["boxiness"] is not None else 1.0)
    # only cache a COMPLETE sweep: a truncated one would freeze the pending pairs
    if not spent:
        ds.cache["shadow_pairs"] = out
    return out


# dependence matrices are computed once per sampler on a subsample (ds.cache)
_DEPENDENCE_N = 1500   # subsample size: distance correlation is O(n^2) in memory


def _double_centered(x: np.ndarray) -> np.ndarray:
    """Double-centered pairwise-distance matrix of a 1-D variable (for dCov)."""
    D = np.abs(x[:, None] - x[None, :])
    return D - D.mean(0, keepdims=True) - D.mean(1, keepdims=True) + D.mean()


def dependence(ds, sampler: str, n: int = _DEPENDENCE_N) -> dict:
    """Pairwise statistical dependence between the 9 technology axes, three ways:

    - **distance correlation** (dcor): 0 iff independent, [0,1], catches nonlinear /
      non-monotonic coupling. The statistical analogue of the geometric facet
      'boxiness' — what actually trades off in the uniform near-optimal cloud.
    - **mutual information** (mi): k-NN (KSG-style) estimate, no binning; raw nats.
    - **pearson**: the linear baseline (near-useless here — included for contrast).

    All three are affine-invariant per axis, so normalized vs physical units give
    identical results; computed on a deterministic subsample (dCor is O(n^2)).
    Cached per sampler (the samples are static)."""
    dep_cache = _slot(ds, "dependence", {})       # sampler -> matrices
    if sampler in dep_cache:
        return dep_cache[sampler]

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
    dep_cache[sampler] = result
    return result


def flexibility(ds, constraints=None):
    """Exact remaining feasible [min, max] of every axis under the user's
    constraints (two LPs per axis), in physical units. The MGA 'how far can each
    lever still go' question, answered on the polytope rather than the sample."""
    cons = constraints or []
    if not cons and ds.cache.get("flex_base") is not None:
        return ds.cache["flex_base"]

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
        ds.cache["flex_base"] = result
    return result


def extremes(ds, sampler: str):
    """For every technology, the designs with its minimum / maximum value over the
    near-optimal polytope (one LP per direction) — the classic MGA extreme
    alternatives. Returns physical values + position in the base PCA space."""
    ext_cache = _slot(ds, "extremes", {})         # sampler -> extreme designs
    if sampler in ext_cache:
        return ext_cache[sampler]
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
    ext_cache[sampler] = out
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


# ---- marginals inside the projection onto the shown axes -------------------
# Hiding an axis is a geometric no-op for the FULL-space marginals: the density
# of a uniform cloud on P, read along axis a, is the same whether or not some
# other axis is drawn. That is worldview A, and it is what the violins used to
# show — so disabling an axis changed nothing on screen.
#
# Worldview B instead treats the shown axes AS the design space: it asks for the
# uniform distribution on the projection pi_S(P), i.e. over the reachable
# COMBINATIONS of the axes you kept, each counted once regardless of how many
# ways the hidden axes can realize it. Under B, hiding an axis genuinely reshapes
# the remaining violins, because the fibre weighting disappears.
#
# pi_S(P) has no cheap H-description (Fourier-Motzkin over 219 rows blows up), so
# we sample it directly: hit-and-run in R^k where the chord along a direction is
# found by LP in the LIFTED space. y + t d is in pi_S(P) iff some x in P has
# x_S = y + t d, so
#
#     max / min  t   s.t.  A x <= b,  x_S - t d = y
#
# gives the exact chord endpoints in two LPs per step. Each axis's own range is
# preserved by projection, so these samples share the base cloud's scales.
_BASE_CLOUD_CAP = 20000


def _projection_chord(A, b, idx, y, d):
    """[t_lo, t_hi] such that y + t*d lies in the projection of {Ax<=b} onto idx.

    Variables are (x, t); the equalities pin the projected coordinates to the
    ray. Returns None if the ray misses the projection (should not happen from
    an interior point, but a stalled LP must not be mistaken for a chord)."""
    n = A.shape[1]
    k = len(idx)
    A_ub = np.hstack([A, np.zeros((A.shape[0], 1))])
    A_eq = np.zeros((k, n + 1))
    for p, i in enumerate(idx):
        A_eq[p, i] = 1.0
        A_eq[p, n] = -float(d[p])
    bounds = [(None, None)] * (n + 1)
    out = []
    for sign in (-1.0, 1.0):                     # max t, then min t
        c = np.zeros(n + 1)
        c[n] = sign
        res = linprog(c, A_ub=A_ub, b_ub=b, A_eq=A_eq, b_eq=np.asarray(y, float),
                      bounds=bounds, method="highs")
        if not res.success:
            return None
        out.append(float(res.x[n]))
    t_hi, t_lo = out
    return (t_lo, t_hi) if t_hi - t_lo > 1e-12 else None


def projected_marginals(ds, axes, n: int = 1200, seed: int = 42,
                        burn: int = 40, thin: int = 2):
    """Uniform sample of pi_S(P) restricted to `axes`, in PHYSICAL units.

    Cached per axis SET (order-independent): the caller re-asks on every toggle,
    and toggling an axis back should not pay for the sweep twice."""
    idx = [ds.axes.index(a) for a in axes if a in ds.axes]
    k = len(idx)
    if k == 0:
        return {"axes": [], "values": [], "n": 0, "method": "empty"}

    names = [ds.axes[i] for i in idx]
    # Every axis shown => pi_S(P) == P, so the base cloud already IS the answer,
    # and no projection sampling is needed. Capped and seeded on the way out: the
    # cloud runs to 100k designs and a caller asking for a DISTRIBUTION does not
    # need (or want to transfer) all of them. The frontend never takes this path
    # — it already holds the cloud and skips the request entirely — so this is
    # here for direct API/`/docs` callers.
    if k == len(ds.axes):
        vals = ds.get_samples(None, "phys")[:, idx]
        if len(vals) > _BASE_CLOUD_CAP:
            pick = np.random.default_rng(seed).choice(
                len(vals), _BASE_CLOUD_CAP, replace=False)
            vals = vals[np.sort(pick)]
        return {"axes": names, "values": vals.tolist(), "n": int(len(vals)),
                "method": "base_cloud"}

    cache = _slot(ds, "proj_marginals", {})
    key = (tuple(sorted(idx)), int(n), int(seed))
    if key in cache:
        hit = cache[key]
        order = [hit["axes"].index(a) for a in names]
        vals = np.asarray(hit["values"])[:, order]
        return {"axes": names, "values": vals.tolist(), "n": hit["n"],
                "method": hit["method"]}

    A, b = ds.A, ds.b
    x0, radius = chebyshev_center(A, b)
    if x0 is None or radius <= 0:
        return {"axes": names, "values": [], "n": 0, "method": "degenerate"}

    rng = np.random.default_rng(seed)
    y = np.asarray(x0, float)[idx]               # projection of an interior point
    out = np.empty((n, k))
    got = 0
    step = 0
    max_iter = burn + n * thin + 500
    while got < n and step < max_iter:
        step += 1
        d = rng.standard_normal(k)
        d /= np.linalg.norm(d) + 1e-12
        chord = _projection_chord(A, b, idx, y, d)
        if chord is None:
            continue                             # degenerate direction; redraw
        t_lo, t_hi = chord
        y = y + rng.uniform(t_lo, t_hi) * d
        if step > burn and (step - burn) % thin == 0:
            out[got] = y
            got += 1

    vals = out[:got] * ds._scale[idx] + ds._offset[idx]
    method = "hit_and_run_projection" if got else "stalled"
    res = {"axes": names, "values": vals.tolist(), "n": int(got), "method": method}
    cache[key] = res
    return res
