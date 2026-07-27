"""FastAPI app: serves metadata, projections and raw sample values."""
from __future__ import annotations

import io

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .data import (
    DATA_DIR,
    SPACES,
    Dataset,
    friendly_name,
    get_dataset,
    is_default,
    list_polytopes,
    set_active,
    validate_polytope,
)
from .generate import dependence as run_dependence
from .generate import reset_caches
from .generate import extremes as run_extremes
from .generate import flexibility as run_flexibility
from .generate import generate as run_generate
from .generate import shadow as run_shadow
from .generate import shadow_pairs as run_shadow_pairs
from .generate import volume_ratio as run_volume
from .projections import METHODS, project

app = FastAPI(title="Energy Explorer API", version="0.1.0")

# Allow the Vite dev server (and others) during development. In the Docker
# setup nginx proxies /api to the backend, so requests are same-origin there.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _meta_dict(ds) -> dict:
    return {
        "axes": ds.axes,
        "methods": list(METHODS),
        "spaces": list(SPACES),
        "n_samples": ds.n_samples,
        "optimum": {
            "u_star": ds.u_star.tolist(),   # the cost optimum, technologies only
            "epsilon": ds.epsilon,
            "norm": ds.optimum_norm.tolist(),
        },
        "diagnostics": ds.diagnostics,
        "dataset": {"name": ds.name, "is_default": is_default()},
    }


@app.get("/api/meta")
def meta():
    return _meta_dict(get_dataset())


def _load_npz_upload(file: UploadFile, kind: str):
    """Read an uploaded .npz into memory, or raise a 400 with a clear message."""
    if not file.filename or not file.filename.lower().endswith(".npz"):
        raise HTTPException(status_code=400, detail=f"{kind} file must be a .npz")
    try:
        return np.load(io.BytesIO(file.file.read()), allow_pickle=True)
    except Exception as e:  # noqa: BLE001 — surface any npz read failure to the user
        raise HTTPException(status_code=400, detail=f"could not read {kind} .npz: {e}")


# Bounds on the user-requested sample count (mirrored on the landing-page UI).
N_MIN, N_MAX = 1000, 100000


def _clamp_n(n: int) -> int:
    if not (N_MIN <= int(n) <= N_MAX):
        raise HTTPException(status_code=422,
                            detail=f"n_samples must be between {N_MIN} and {N_MAX}")
    return int(n)


def _activate_and_warm(ds: Dataset) -> dict:
    """Make ds the active dataset, clear stale caches, then eagerly precompute the
    heavy views (dependence, facet shadows, extremes, base flexibility) so the UI
    opens instantly. Returns the meta payload."""
    set_active(ds)
    reset_caches()
    run_dependence(ds, "chrrt")
    run_shadow_pairs(ds)       # also fills the per-pair shadow cache
    run_extremes(ds, "chrrt")
    run_flexibility(ds, [])
    return _meta_dict(ds)


@app.get("/api/datasets")
def datasets():
    """Preloaded polytopes available to build from (landing-page picker)."""
    return {"datasets": list_polytopes()}


class BuildPreloadedRequest(BaseModel):
    dataset_id: str
    n_samples: int = 20000


@app.post("/api/build/preloaded")
def build_preloaded(req: BuildPreloadedRequest):
    """Build the active dataset from a preloaded polytope: generate `n_samples`
    hit-and-run samples over it, then precompute every view."""
    n = _clamp_n(req.n_samples)
    stem = req.dataset_id
    path = DATA_DIR / f"{stem}.npz"
    if "/" in stem or ".." in stem or not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown dataset {stem!r}")
    poly = np.load(path, allow_pickle=True)
    try:
        ds = Dataset(poly, name=friendly_name(stem), n_samples=n)
    except Exception as e:  # noqa: BLE001 — construction failures → 422 with context
        raise HTTPException(status_code=422, detail=f"failed to build dataset: {e}")
    return _activate_and_warm(ds)


@app.post("/api/build/upload")
def build_upload(polytope: UploadFile = File(...), n_samples: int = Form(20000)):
    """Build the active dataset from an uploaded polytope .npz (technologies +
    cost, same schema as the shipped polytope), generating `n_samples` samples."""
    n = _clamp_n(n_samples)
    poly = _load_npz_upload(polytope, "polytope")
    problems = validate_polytope(poly)
    if problems:
        raise HTTPException(status_code=422, detail={"errors": problems})
    try:
        ds = Dataset(poly, name=polytope.filename or "uploaded polytope", n_samples=n)
    except Exception as e:  # noqa: BLE001 — construction failures → 422 with context
        raise HTTPException(status_code=422, detail=f"failed to build dataset: {e}")
    return _activate_and_warm(ds)


@app.get("/api/projection")
def projection(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    sample: int | None = Query(None, description="optionally downsample to N points"),
):
    ds = get_dataset()
    try:
        pts, ev, cached, opt, comp = project(ds, method, sampler, dims)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 425 Too Early: the expensive projection hasn't been built yet.
        raise HTTPException(status_code=425, detail=str(e))

    n = pts.shape[0]
    index = np.arange(n)
    if sample is not None and sample < n:
        rng = np.random.default_rng(42)
        index = np.sort(rng.choice(n, size=sample, replace=False))
        pts = pts[index]

    return {
        "method": method,
        "sampler": sampler,
        "dims": dims,
        "points": pts.astype(float).tolist(),
        "index": index.astype(int).tolist(),
        "optimum": opt.astype(float).tolist() if opt is not None else None,
        "explained_variance": ev,
        # PCA loadings: per-component weights over the technology axes, so the UI
        # can name what each axis represents. None for t-SNE/UMAP.
        "components": comp.astype(float).tolist() if comp is not None else None,
        "feature_names": [ds.axes[i] for i in ds.tech_idx],
        "cached": cached,
    }


@app.get("/api/color")
def color(
    sampler: str = Query("chrrt"),
    field: str = Query("nuclear", description="technology axis name"),
    space: str = Query("phys"),
):
    """Per-sample scalar used to color the scatter, aligned to sample row order."""
    ds = get_dataset()
    if field not in ds.axes:
        raise HTTPException(status_code=400, detail=f"unknown field {field!r}")
    try:
        data = ds.get_samples(sampler, space)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    vals = data[:, ds.axes.index(field)]
    return {
        "field": field,
        "values": vals.astype(float).tolist(),
        "min": float(vals.min()),
        "max": float(vals.max()),
        "categorical": False,
    }


@app.get("/api/samples")
def samples(
    sampler: str = Query("chrrt"),
    space: str = Query("phys"),
    fields: str = Query(..., description="comma-separated axis names"),
):
    ds = get_dataset()
    try:
        data = ds.get_samples(sampler, space)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    names = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        cols = [ds.axes.index(n) for n in names]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown field: {e}")

    return {
        "fields": names,
        "values": data[:, cols].astype(float).tolist(),
        "index": list(range(data.shape[0])),
    }


@app.get("/api/extremes")
def extremes(sampler: str = Query("chrrt")):
    """MGA extreme designs: per technology, its min and max over the polytope
    (LP vertices), as physical values + base-PCA coordinates."""
    ds = get_dataset()
    return {"sampler": sampler, "extremes": run_extremes(ds, sampler)}


class Constraint(BaseModel):
    axis: str
    min: float | None = None
    max: float | None = None


class GenerateRequest(BaseModel):
    sampler: str = "chrrt"
    n: int = 2000
    seed: int = 42
    constraints: list[Constraint] = []


class ShadowRequest(BaseModel):
    x: str
    y: str
    constraints: list[Constraint] = []


class FlexibilityRequest(BaseModel):
    constraints: list[Constraint] = []


@app.get("/api/dependence")
def dependence(sampler: str = Query("chrrt")):
    """Pairwise dependence between the 10 axes: distance correlation, mutual
    information, and Pearson. Distance correlation/MI catch the nonlinear coupling
    that Pearson misses in the uniform near-optimal cloud."""
    ds = get_dataset()
    return run_dependence(ds, sampler)


@app.get("/api/shadow_pairs")
def shadow_pairs():
    """All axis pairs ranked by shadow interestingness (ascending boxiness)."""
    return {"pairs": run_shadow_pairs(get_dataset())}


@app.post("/api/shadow")
def shadow(req: ShadowRequest):
    """Exact 2-D shadow polygon of the (optionally constrained) polytope."""
    ds = get_dataset()
    try:
        return run_shadow(ds, req.x, req.y, [c.model_dump() for c in req.constraints])
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/flexibility")
def flexibility(req: FlexibilityRequest):
    """Exact remaining [min, max] per axis under the given constraints."""
    ds = get_dataset()
    return run_flexibility(ds, [c.model_dump() for c in req.constraints])


@app.post("/api/volume")
def volume(req: FlexibilityRequest):
    """Relative volume of the constrained near-optimal region vs the whole space
    ('how much of the design space survives these constraints'). Subset-simulation
    estimate so it stays accurate deep in the tail, where the uniform-sample
    fraction the UI computes lands zero points."""
    ds = get_dataset()
    return run_volume(ds, [c.model_dump() for c in req.constraints])


@app.post("/api/generate")
def generate(req: GenerateRequest):
    """Add the user's constraints to the polytope and re-sample fresh candidate
    designs from the constrained near-optimal region (projected into PCA space)."""
    ds = get_dataset()
    cons = [c.model_dump() for c in req.constraints]
    return run_generate(ds, req.sampler, req.n, cons, seed=req.seed)


@app.get("/api/clusters")
def clusters(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    k: int = Query(6, ge=2, le=12),
):
    """K-means clusters of the projected designs, each characterized by the
    technologies that most distinguish it from the overall mean. Returns centroid
    positions in projection space (so the UI can label them on the scatter)."""
    from sklearn.cluster import KMeans

    ds = get_dataset()
    try:
        pts, _, _, _, _ = project(ds, method, sampler, dims)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=425, detail=str(e))

    km = KMeans(n_clusters=k, random_state=42, n_init=5).fit(pts)
    labels = km.labels_
    centroids = km.cluster_centers_

    tech = ds.get_samples(sampler, "phys")[:, ds.tech_idx]
    tech_names = [ds.axes[i] for i in ds.tech_idx]
    gmean = tech.mean(0)
    gstd = tech.std(0) + 1e-9

    out = []
    for c in range(k):
        m = labels == c
        cmean = tech[m].mean(0)
        z = (cmean - gmean) / gstd
        order = np.argsort(-np.abs(z))[:3]
        out.append({
            "x": float(centroids[c, 0]),
            "y": float(centroids[c, 1]),
            "count": int(m.sum()),
            "top": [
                {"name": tech_names[i], "z": float(z[i]), "value": float(cmean[i])}
                for i in order
            ],
        })
    return {"method": method, "sampler": sampler, "k": k, "clusters": out}
