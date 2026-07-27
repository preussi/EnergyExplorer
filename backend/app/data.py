"""Loads the polytope + sample data once and exposes typed accessors.

The design space is **9-dimensional: the 9 technologies** (cost is not a design
axis). The upstream polytope carries cost as a 10th variable purely to encode
near-optimality (cost ≤ (1+ε)·c*); we eliminate it once at load time via
Fourier–Motzkin projection, leaving a clean 9-D technology polytope with
near-optimality baked in. Samples are fmax-normalized 9-D technology vectors
(`phys = norm * u_star`) from a single hit-and-run sampler.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

SPACES = ("norm", "phys")
COST_AXIS = "net_present_cost"
# Upstream ships ~400k samples; that is far more than the UI needs and would make
# /api/samples a ~50MB payload. Cap to a seeded, deterministic subset (plenty for
# projections, clustering, dependence and the marginal/conditional violins).
MAX_SAMPLES = int(os.environ.get("MAX_SAMPLES", "400000"))
# We now generate the sample cloud ourselves from the polytope (uniform
# hit-and-run) rather than loading a samples file. This is the default count used
# when a caller doesn't specify one (e.g. the lazily-built file default).
DEFAULT_N_SAMPLES = int(os.environ.get("DEFAULT_N_SAMPLES", "20000"))
# Human-friendly labels for the preloaded polytope files (falls back to the stem).
_FRIENDLY = {"polytope_13": "Swiss energy system (v13)"}


def friendly_name(stem: str) -> str:
    return _FRIENDLY.get(stem, stem)


def _resolve(data_dir: Path, stem: str, exclude: str = "") -> Path:
    """Find `{stem}.npz`, else the newest versioned `{stem}_*.npz`.

    The upstream data is delivered as versioned files (e.g. `polytope_13.npz`),
    so we pick the highest suffix rather than hardcoding a name. `exclude` drops
    siblings whose name contains it (so the `polytope` glob ignores
    `polytope_samples_*`).
    """
    exact = data_dir / f"{stem}.npz"
    if exact.exists():
        return exact
    cands = sorted(
        p for p in data_dir.glob(f"{stem}_*.npz")
        if not (exclude and exclude in p.name)
    )
    if not cands:
        raise FileNotFoundError(f"no {stem}.npz or {stem}_*.npz in {data_dir}")
    return cands[-1]


def _eliminate(A: np.ndarray, b: np.ndarray, col: int) -> tuple[np.ndarray, np.ndarray]:
    """Fourier–Motzkin: project out variable `col` from {x : A x ≤ b}.

    Rows with a zero coefficient keep their other columns; each positive/negative
    pair is combined into one cost-free constraint. Here only ~1 row has a
    positive cost coefficient, so this stays ~O(#rows), not exponential.
    """
    coef = A[:, col]
    keep = [c for c in range(A.shape[1]) if c != col]
    pos = np.where(coef > 1e-12)[0]
    neg = np.where(coef < -1e-12)[0]
    zero = np.where(np.abs(coef) <= 1e-12)[0]

    rows: list[np.ndarray] = [A[r][keep] for r in zero]
    rhs: list[float] = [float(b[r]) for r in zero]
    for i in pos:
        ap = coef[i]
        for j in neg:
            an = coef[j]
            # (-an)>0 and ap>0 → a nonnegative combination that cancels `col`
            rows.append((-an) * A[i][keep] + ap * A[j][keep])
            rhs.append((-an) * b[i] + ap * b[j])
    return np.asarray(rows, dtype=float), np.asarray(rhs, dtype=float)


def _keys(npz) -> set[str]:
    """Key set of an ``np.load`` result (or any mapping)."""
    return set(getattr(npz, "files", None) or npz.keys())


class Dataset:
    """In-memory view of a polytope in clean 9-D technology space, with a sample
    cloud. Samples are generated from the polytope (uniform hit-and-run) unless a
    ``samp`` mapping is passed (back-compat / tests). The same code serves the
    file-based default and user-uploaded polytopes. Use :func:`validate_polytope`
    to check an upload before constructing.
    """

    def __init__(self, poly, samp=None, name: str = "",
                 n_samples: int | None = None, seed: int = 42):
        self.name = name

        names = [str(n) for n in poly["name_list"]]        # 10 (last = cost)
        cost_col = names.index(COST_AXIS)
        self.axes = [n for i, n in enumerate(names) if i != cost_col]  # 9 techs
        # all axes are technologies now; kept for callers that split tech vs cost
        self.tech_idx = list(range(len(self.axes)))

        # Project cost out of the polytope → clean 9-D near-optimal technology body.
        A10 = np.asarray(poly["A"], dtype=float)
        b10 = np.asarray(poly["b"], dtype=float)
        self.A, self.b = _eliminate(A10, b10, cost_col)     # (~219, 9)
        keep = [i for i in range(A10.shape[1]) if i != cost_col]
        self.X = (np.asarray(poly["X"], dtype=float)[:, keep]
                  if "X" in _keys(poly) else np.empty((0, len(self.axes))))

        self.c_star = float(np.asarray(poly["c_star"])) if "c_star" in _keys(poly) else float("nan")
        self.epsilon = float(np.asarray(poly["epsilon"])) if "epsilon" in _keys(poly) else 0.1

        # fmax normalization: phys = norm * u_star (per-axis maxima), offset 0.
        self.norm_max = np.asarray(poly["u_star"], dtype=float)      # (9,)
        self._scale = self.norm_max.copy()                          # (9,)
        self._offset = np.zeros(len(self.axes))                     # (9,)

        # The real cost optimum (technologies only).
        self.optimum_tech = np.asarray(poly["z_star"], dtype=float)  # (9,)
        self.u_star = self.optimum_tech      # back-compat alias: this IS the optimum
        self.optimum_norm = (self.optimum_tech - self._offset) / self._scale  # (9,)

        # ---- samples: 9-D fmax-normalized technologies ----
        # Preferred path: generate the cloud ourselves (uniform hit-and-run over
        # the projected polytope), so no samples file is needed. A samp mapping is
        # still accepted for back-compat / tests.
        if samp is not None:
            samp_keys = _keys(samp)
            S = np.asarray(samp["samples"], dtype=float)            # (N, 9) normalized
            chain = (np.asarray(samp["chain_id"], dtype=int)
                     if "chain_id" in samp_keys else np.zeros(len(S), dtype=int))
            if len(S) > MAX_SAMPLES:
                rng = np.random.default_rng(42)
                sub = np.sort(rng.choice(len(S), MAX_SAMPLES, replace=False))
                S, chain = S[sub], chain[sub]
            self._chain = chain
            self._n_chains = (int(np.asarray(samp["n_chains"]))
                              if "n_chains" in samp_keys else int(chain.max()) + 1)

            def _diag(key: str) -> list:
                return np.asarray(samp[key]).tolist() if key in samp_keys else []
            self.diagnostics = {
                "method": str(samp["method"]) if "method" in samp.files else "hit_and_run",
                "rhat": _diag("rhat"),
                "ess": _diag("ess_bulk"),
            }
        else:
            # Uniform over {x : A x <= b} via hit-and-run from the Chebyshev center.
            # Local import avoids a module-load cycle (generate imports nothing here).
            from .generate import chebyshev_center, hit_and_run
            n = int(n_samples if n_samples is not None else DEFAULT_N_SAMPLES)
            x0, radius = chebyshev_center(self.A, self.b)
            if x0 is None or radius <= 0:
                raise ValueError("polytope has no interior; cannot generate samples")
            S = hit_and_run(self.A, self.b, n, x0, seed=seed)
            if len(S) == 0:
                raise ValueError("sample generation produced no points")
            self._chain = np.zeros(len(S), dtype=int)
            self._n_chains = 1
            # single generated chain — rhat/ess convergence stats aren't meaningful
            self.diagnostics = {"method": "hit_and_run", "rhat": [], "ess": []}

        self._norm = S
        self._phys = S * self._scale + self._offset

    @property
    def n_samples(self) -> int:
        return self._norm.shape[0]

    @property
    def optimum_norm_tech(self) -> np.ndarray:
        """Optimum in normalized technology space (9,) — input to projections."""
        return self.optimum_norm

    def get_samples(self, sampler: str | None = None, space: str = "phys") -> np.ndarray:
        """9-D technology samples. `sampler` is ignored (single sampler now)."""
        if space not in SPACES:
            raise KeyError(f"unknown space {space!r}; expected {SPACES}")
        return self._phys if space == "phys" else self._norm

    def chain_ids(self) -> np.ndarray:
        """MCMC chain index per sample (for convergence diagnostics)."""
        return self._chain


# ---- schema validation for uploaded polytopes --------------------------------
# Keys the loader genuinely needs; the rest (X, c_star, epsilon…) are optional and
# default gracefully in Dataset.__init__. Samples are generated, never uploaded.
REQUIRED_POLY = ("name_list", "A", "b", "u_star", "z_star")


def validate_polytope(poly) -> list[str]:
    """Return a list of human-readable problems with an uploaded polytope; an empty
    list means it is safe to build a :class:`Dataset` from it."""
    problems: list[str] = []
    pk = _keys(poly)
    for k in REQUIRED_POLY:
        if k not in pk:
            problems.append(f"polytope file is missing required key '{k}'")
    if problems:
        return problems  # can't inspect shapes safely until keys exist

    names = [str(n) for n in poly["name_list"]]
    if COST_AXIS not in names:
        problems.append(f"name_list must include the cost axis '{COST_AXIS}'")
        return problems
    n_tech = len(names) - 1

    A = np.asarray(poly["A"])
    if A.ndim != 2 or A.shape[1] != len(names):
        problems.append(f"A must be 2-D with {len(names)} columns; got shape {A.shape}")
    b = np.asarray(poly["b"]).ravel()
    if A.ndim == 2 and b.size != A.shape[0]:
        problems.append(f"b must have {A.shape[0]} entries (one per A row); got {b.size}")

    for key in ("u_star", "z_star"):
        v = np.asarray(poly[key]).ravel()
        if v.size != n_tech:
            problems.append(f"{key} must have {n_tech} entries (one per technology); got {v.size}")
    return problems


# ---- active-dataset management (default file data vs. an uploaded override) ----
_DEFAULT: Dataset | None = None   # lazily-built file-based dataset
_ACTIVE: Dataset | None = None    # uploaded override; None → use the default


def list_polytopes() -> list[dict]:
    """Preloaded polytope files available in ``DATA_DIR`` (samples files excluded),
    each as ``{id, name, n_axes}`` for the landing-page picker."""
    out: list[dict] = []
    for p in sorted(DATA_DIR.glob("polytope*.npz")):
        if "samples" in p.name:
            continue
        try:
            npz = np.load(p, allow_pickle=True)
            names = [str(n) for n in npz["name_list"]]
            n_axes = len(names) - 1 if COST_AXIS in names else len(names)
        except Exception:  # noqa: BLE001 — skip unreadable/invalid files
            continue
        out.append({"id": p.stem, "name": friendly_name(p.stem), "n_axes": int(n_axes)})
    return out


def load_default() -> Dataset:
    """The file-based dataset resolved from ``DATA_DIR`` (built once, cached).
    Samples are generated from the polytope — no samples file is read."""
    global _DEFAULT
    if _DEFAULT is None:
        poly_path = _resolve(DATA_DIR, "polytope", exclude="samples")
        name = friendly_name(poly_path.stem)
        print(f"[data] loading default {poly_path.name} (+{DEFAULT_N_SAMPLES} generated samples)", flush=True)
        _DEFAULT = Dataset(np.load(poly_path, allow_pickle=True),
                           name=name, n_samples=DEFAULT_N_SAMPLES)
    return _DEFAULT


def get_dataset() -> Dataset:
    """The dataset every endpoint reads: the uploaded override if one is active,
    otherwise the shipped default."""
    return _ACTIVE if _ACTIVE is not None else load_default()


def set_active(ds: Dataset | None) -> None:
    global _ACTIVE
    _ACTIVE = ds


def reset_active() -> None:
    """Drop any uploaded dataset and fall back to the shipped default."""
    set_active(None)


def is_default() -> bool:
    return _ACTIVE is None
