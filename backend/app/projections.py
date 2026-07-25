"""Dimensionality reduction. Only PCA is offered (computed live); it has an exact
out-of-sample transform, so the optimum and the component loadings come for free.
t-SNE/UMAP were removed — the linear projections (PCA + the frontend's star
coordinates) are the honest choice for this convex body.
"""
from __future__ import annotations

import numpy as np
from sklearn.decomposition import PCA

from .data import Dataset

METHODS = ("pca",)


def _matrix(ds: Dataset, sampler: str) -> np.ndarray:
    """Normalized samples over the technology axes."""
    return ds.get_samples(sampler, "norm")[:, ds.tech_idx]


def pca(ds: Dataset, sampler: str, dims: int = 2):
    """Returns (points, explained_variance, optimum_projection, components)."""
    X = _matrix(ds, sampler)
    model = PCA(n_components=dims, random_state=42)
    pts = model.fit_transform(X)
    opt = model.transform(ds.optimum_norm_tech.reshape(1, -1))[0]
    return pts, model.explained_variance_ratio_.tolist(), opt, model.components_


def project(ds: Dataset, method: str, sampler: str, dims: int = 2):
    """Return (points, explained_variance, cached=False, optimum, components)."""
    if method not in METHODS:
        raise KeyError(f"unknown method {method!r}; expected {METHODS}")
    pts, ev, opt, comp = pca(ds, sampler, dims)
    return pts, ev, False, opt, comp
