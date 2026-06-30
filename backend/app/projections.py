"""Dimensionality reduction with on-disk caching.

PCA is cheap and computed live. t-SNE / UMAP are expensive on 20k points and
are read from a cache built ahead of time (see `build_cache`). Projections are
computed on the *normalized* space, over the 9 technology axes only (cost is
treated as a color channel, not geometry).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA

from .data import Dataset

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/app/cache"))
METHODS = ("pca", "tsne", "umap")


def _matrix(ds: Dataset, sampler: str) -> np.ndarray:
    """Normalized samples over technology axes only."""
    return ds.get_samples(sampler, "norm")[:, ds.tech_idx]


def _cache_path(method: str, sampler: str, dims: int) -> Path:
    return CACHE_DIR / f"{method}_{sampler}_{dims}d.npy"


def _opt_path(method: str, sampler: str, dims: int) -> Path:
    """Sidecar holding the optimum's embedded position for a cached projection."""
    return CACHE_DIR / f"{method}_{sampler}_{dims}d.opt.npy"


def pca(ds: Dataset, sampler: str, dims: int = 2):
    """Returns (points, explained_variance, optimum_projection, components)."""
    X = _matrix(ds, sampler)
    model = PCA(n_components=dims, random_state=42)
    pts = model.fit_transform(X)
    opt = model.transform(ds.optimum_norm_tech.reshape(1, -1))[0]
    return pts, model.explained_variance_ratio_.tolist(), opt, model.components_


def project(ds: Dataset, method: str, sampler: str, dims: int = 2):
    """Return (points, explained_variance | None, cached, optimum | None, components | None).

    Raises KeyError for unknown method, FileNotFoundError if an expensive method
    has no cache yet. PCA has an exact out-of-sample transform (so optimum and
    component loadings are returned); t-SNE/UMAP embed the optimum jointly at
    build time (loaded from a sidecar) and have no interpretable loadings.
    """
    if method not in METHODS:
        raise KeyError(f"unknown method {method!r}; expected {METHODS}")

    if method == "pca":
        pts, ev, opt, comp = pca(ds, sampler, dims)
        return pts, ev, False, opt, comp

    path = _cache_path(method, sampler, dims)
    if not path.exists():
        raise FileNotFoundError(
            f"{method} projection for sampler={sampler} dims={dims} not cached. "
            f"Run the cache builder first."
        )
    opt_path = _opt_path(method, sampler, dims)
    opt = None
    if opt_path.exists():
        arr = np.load(opt_path)
        opt = arr[0] if arr.ndim == 2 else arr
    return np.load(path), None, True, opt, None


def build_cache(
    ds: Dataset,
    methods=("tsne", "umap"),
    samplers=None,
    dims=2,
    force=False,
) -> list[str]:
    """Precompute and store the expensive projections. Called by a one-off job.

    Skips any (method, sampler, dims) whose .npy already exists unless force=True,
    so re-running only fills in what's missing.
    """
    import time

    from sklearn.manifold import TSNE

    # Only chrrt is exposed in the UI, so by default build the cache for it alone
    # (halves build time / image size). Pass samplers=[...] to override.
    samplers = samplers or ["chrrt"]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for sampler in samplers:
        X = _matrix(ds, sampler)
        # Append the cost optimum so t-SNE/UMAP (which have no out-of-sample
        # transform) embed it jointly; we split its position back out below.
        X_aug = np.vstack([X, ds.optimum_norm_tech.reshape(1, -1)])
        for method in methods:
            path = _cache_path(method, sampler, dims)
            opt_path = _opt_path(method, sampler, dims)
            if path.exists() and opt_path.exists() and not force:
                print(f"  [skip] {path.name} (+optimum) already exists", flush=True)
                written.append(str(path))
                continue
            print(f"  [{method}] {sampler} {dims}D on {X.shape[0]}x{X.shape[1]} (+optimum) ...",
                  end=" ", flush=True)
            t0 = time.perf_counter()
            if method == "tsne":
                emb = TSNE(n_components=dims, random_state=42, init="pca").fit_transform(X_aug)
            elif method == "umap":
                try:
                    import umap  # umap-learn; optional dependency
                except ImportError:
                    print("skipped (umap-learn not installed)", flush=True)
                    continue
                emb = umap.UMAP(n_components=dims, random_state=42).fit_transform(X_aug)
            else:
                continue
            pts, opt = emb[:-1], emb[-1:]
            np.save(path, np.asarray(pts, dtype=np.float32))
            np.save(opt_path, np.asarray(opt, dtype=np.float32))
            written.append(str(path))
            print(f"done in {time.perf_counter() - t0:.1f}s -> {path.name}", flush=True)
    (CACHE_DIR / "manifest.json").write_text(json.dumps({"files": sorted(set(written))}, indent=2))
    return written


if __name__ == "__main__":
    import sys

    from .data import get_dataset

    force = "--force" in sys.argv
    print(f"Building projection cache (force={force}) into {CACHE_DIR} ...", flush=True)
    files = build_cache(get_dataset(), force=force)
    print("Cache contents:", *sorted(set(files)), sep="\n  ")
