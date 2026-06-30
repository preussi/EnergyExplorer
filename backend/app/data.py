"""Loads the polytope + sample data once and exposes typed accessors.

The data is 10-dimensional: 9 technologies + net_present_cost (last axis).
Samples come from two MCMC samplers (chrrt, har), each 4 chains x 5000 draws,
in normalized (`_norm`, ~[0,1]) and physical (`_phys`) units.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))

SAMPLERS = ("chrrt", "har")
SPACES = ("norm", "phys")
COST_AXIS = "net_present_cost"


def _resolve(data_dir: Path, stem: str, exclude: str = "") -> Path:
    """Find `{stem}.npz`, else the newest versioned `{stem}_*.npz`.

    The upstream data is delivered as versioned files (e.g. `polytope_08.npz`),
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


class Dataset:
    """In-memory view of the two .npz files."""

    def __init__(self, data_dir: Path = DATA_DIR):
        poly_path = _resolve(data_dir, "polytope", exclude="samples")
        samp_path = _resolve(data_dir, "polytope_samples")
        print(f"[data] loading {poly_path.name} + {samp_path.name}", flush=True)
        poly = np.load(poly_path, allow_pickle=True)
        samp = np.load(samp_path, allow_pickle=True)

        self.A = poly["A"]                       # (172, 10)
        self.b = poly["b"]                       # (172,)
        self.X = poly["X"]                       # (153, 10) points inside polytope
        self.axes = [str(n) for n in poly["name_list"]]  # 10 labels
        self.cost_idx = self.axes.index(COST_AXIS)
        self.tech_idx = [i for i in range(len(self.axes)) if i != self.cost_idx]

        # samples[sampler][space] -> (20000, 10)
        self.samples: dict[str, dict[str, np.ndarray]] = {}
        for s in SAMPLERS:
            self.samples[s] = {
                sp: samp[f"{s}_{sp}"].reshape(-1, len(self.axes)) for sp in SPACES
            }

        cfg = samp["config"].item()
        self.config = cfg
        self.c_star = float(cfg.get("c_star"))
        self.epsilon = float(cfg.get("epsilon"))

        # The cost optimum, technologies only (9,). NOTE: `u_star` in these files
        # is NOT the optimum — it is the per-axis normalization maxima (hence it
        # maps to the unit corner). The real optimum is `z_star` (added in the
        # v08 files). Fall back to u_star only for older files that lack z_star.
        self.norm_max = np.asarray(samp["u_star"], dtype=float)  # per-axis maxima
        if "z_star" in poly.files:
            self.optimum_tech = np.asarray(poly["z_star"], dtype=float)
        elif "z_star_physical" in cfg:
            self.optimum_tech = np.asarray(cfg["z_star_physical"], dtype=float)[: len(self.tech_idx)]
        else:
            print("[data] WARNING: no z_star found; falling back to u_star "
                  "(normalization maxima) as the optimum — overlay will be wrong",
                  flush=True)
            self.optimum_tech = self.norm_max
        # back-compat alias: callers/serializers still read `u_star` as the optimum
        self.u_star = self.optimum_tech

        # The norm<->phys map is a per-axis linear rescaling (verified diagonal
        # & exactly affine). Recover scale/offset so phys = norm*scale + offset.
        ref_norm = self.samples["chrrt"]["norm"]
        ref_phys = self.samples["chrrt"]["phys"]
        nmean, pmean = ref_norm.mean(0), ref_phys.mean(0)
        nvar = ((ref_norm - nmean) ** 2).mean(0)
        cov = ((ref_norm - nmean) * (ref_phys - pmean)).mean(0)
        self._scale = cov / nvar                 # (10,)
        self._offset = pmean - self._scale * nmean

        # Optimum as a full 10-vector in physical units, then in norm space.
        opt_phys = np.empty(len(self.axes))
        opt_phys[self.tech_idx] = self.optimum_tech
        opt_phys[self.cost_idx] = self.c_star
        self.optimum_norm = (opt_phys - self._offset) / self._scale  # (10,)

        self.diagnostics = {
            "chrrt": {"rhat": samp["chrrt_rhat"].tolist(), "ess": samp["chrrt_ess"].tolist()},
            "har": {"rhat": samp["har_rhat"].tolist(), "ess": samp["har_ess"].tolist()},
        }

    @property
    def n_samples(self) -> int:
        return self.samples["chrrt"]["norm"].shape[0]

    def get_samples(self, sampler: str, space: str) -> np.ndarray:
        if sampler not in SAMPLERS:
            raise KeyError(f"unknown sampler {sampler!r}; expected {SAMPLERS}")
        if space not in SPACES:
            raise KeyError(f"unknown space {space!r}; expected {SPACES}")
        return self.samples[sampler][space]

    def cost(self, sampler: str, space: str = "phys") -> np.ndarray:
        """Per-sample cost column (for color-by-cost)."""
        return self.get_samples(sampler, space)[:, self.cost_idx]

    @property
    def optimum_norm_tech(self) -> np.ndarray:
        """Optimum in normalized technology space (9,) — input to projections."""
        return self.optimum_norm[self.tech_idx]

    def chain_ids(self) -> np.ndarray:
        """Chain index (0..n_chains-1) per flattened sample, for color-by-chain."""
        n_chains = int(self.config.get("n_chains", 4))
        per = self.n_samples // n_chains
        return np.repeat(np.arange(n_chains), per)


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    return Dataset()
