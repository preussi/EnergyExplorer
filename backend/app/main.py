"""FastAPI app: serves metadata, projections and raw sample values."""
from __future__ import annotations

import io
from contextlib import asynccontextmanager

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import sessions
from .data import (
    DATA_DIR,
    MAX_AXES,
    SPACES,
    WARN_AXES,
    Dataset,
    friendly_name,
    get_dataset,
    list_polytopes,
    validate_polytope,
)
from .generate import dependence as run_dependence
from .generate import extremes as run_extremes
from .generate import flexibility as run_flexibility
from .generate import projected_marginals as run_marginals
from .generate import generate as run_generate
from .generate import shadow as run_shadow
from .generate import shadow_pairs as run_shadow_pairs
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
    return {"status": "ok", "sessions": sessions.stats()}


# ---- per-request dataset resolution (multi-tenancy) ----
# Every endpoint reads *the dataset named by this request*, never module state:
# that is what lets two users hold two different polytopes at the same time. The
# id rides along as the `X-Dataset-Id` header (set once in the frontend's fetch
# wrapper) or as `?ds=` so a URL can be shared and curl/`/docs` stay usable.
# Id-less requests get the shipped default, which keeps the API explorable.
def current_id(
    ds: str | None = Query(None, alias="ds", description="dataset session id"),
    x_dataset_id: str | None = Header(None, alias="X-Dataset-Id"),
) -> str | None:
    return ds or x_dataset_id or None


def current_dataset(sid: str | None = Depends(current_id)) -> Dataset:
    if not sid:
        return get_dataset()
    try:
        return sessions.get(sid)
    except sessions.UnknownSession:
        # 404 is the signal the frontend uses to fall back to the landing page
        raise HTTPException(status_code=404,
                            detail=f"unknown or expired dataset session {sid!r}")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    sessions.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    sessions.sweep()          # drop recipes past SESSION_TTL_DAYS
    yield


app.router.lifespan_context = _lifespan


def _meta_dict(ds, sid: str | None = None) -> dict:
    return {
        "axes": ds.axes,
        # physical unit per axis, aligned with `axes` (GW / GWh / ktCO2eq per hour)
        "units": getattr(ds, "units", [""] * len(ds.axes)),
        # axes that lump several technologies together → their members
        "axis_members": getattr(ds, "axis_members", {}),
        "methods": list(METHODS),
        "spaces": list(SPACES),
        "n_samples": ds.n_samples,
        "optimum": {
            "u_star": ds.u_star.tolist(),   # the cost optimum, technologies only
            "epsilon": ds.epsilon,
            "norm": ds.optimum_norm.tolist(),
            "c_star": ds.c_star,
            "cost_unit": getattr(ds, "cost_unit", ""),
        },
        "diagnostics": ds.diagnostics,
        # `id` is what the frontend persists (URL + localStorage) to come back to
        # this exact dataset after a refresh; absent for the shipped default.
        "dataset": {"name": ds.name, "is_default": sid is None, "id": sid},
    }


@app.get("/api/meta")
def meta(ds: Dataset = Depends(current_dataset), sid: str | None = Depends(current_id)):
    return _meta_dict(ds, sid)


def _load_npz_upload(file: UploadFile, kind: str, raw: bytes | None = None):
    """Read an uploaded .npz into memory, or raise a 400 with a clear message."""
    if not file.filename or not file.filename.lower().endswith(".npz"):
        raise HTTPException(status_code=400, detail=f"{kind} file must be a .npz")
    try:
        return np.load(io.BytesIO(raw if raw is not None else file.file.read()),
                       allow_pickle=True)
    except Exception as e:  # noqa: BLE001 — surface any npz read failure to the user
        raise HTTPException(status_code=400, detail=f"could not read {kind} .npz: {e}")


# Bounds on the user-requested sample count (mirrored on the landing-page UI).
N_MIN, N_MAX = 1000, 100000


def _require_n(n: int) -> int:
    """Reject an out-of-range sample count (422) rather than silently clamping it —
    a caller asking for 200k should learn it didn't get 200k."""
    if not (N_MIN <= int(n) <= N_MAX):
        raise HTTPException(status_code=422,
                            detail=f"n_samples must be between {N_MIN} and {N_MAX}")
    return int(n)


def _warm(ds: Dataset, sid: str) -> dict:
    """Eagerly precompute the heavy views (dependence, facet shadows, extremes,
    base flexibility) into this dataset's own caches, so every view opens
    instantly. Returns the meta payload carrying the session id."""
    run_dependence(ds, "chrrt")
    run_shadow_pairs(ds)       # also fills the per-pair shadow cache
    run_extremes(ds, "chrrt")
    run_flexibility(ds, [])
    return _meta_dict(ds, sid)


@app.get("/api/datasets")
def datasets():
    """Preloaded polytopes available to build from (landing-page picker), plus the
    axis limits so the page can warn about a slow build before it starts rather
    than after."""
    return {"datasets": list_polytopes(), "max_axes": MAX_AXES, "warn_axes": WARN_AXES}


class BuildPreloadedRequest(BaseModel):
    dataset_id: str
    n_samples: int = 20000


@app.post("/api/build/preloaded")
def build_preloaded(req: BuildPreloadedRequest):
    """Build the active dataset from a preloaded polytope: generate `n_samples`
    hit-and-run samples over it, then precompute every view."""
    n = _require_n(req.n_samples)
    stem = req.dataset_id
    path = DATA_DIR / f"{stem}.npz"
    if "/" in stem or ".." in stem or not path.exists():
        raise HTTPException(status_code=404, detail=f"unknown dataset {stem!r}")
    # Same guards as the upload path: a corrupt or off-schema shipped .npz should
    # reach the landing page as a readable 422, not an opaque 500 from np.load.
    try:
        poly = np.load(path, allow_pickle=True)
    except Exception as e:  # noqa: BLE001 — surface any npz read failure to the user
        raise HTTPException(status_code=422, detail=f"could not read dataset {stem!r}: {e}")
    problems = validate_polytope(poly)
    if problems:
        raise HTTPException(status_code=422, detail={"errors": problems})
    try:
        sid, ds = sessions.create(poly, source="preloaded", stem=stem,
                                  n_samples=n, name=friendly_name(stem))
    except Exception as e:  # noqa: BLE001 — construction failures → 422 with context
        raise HTTPException(status_code=422, detail=f"failed to build dataset: {e}")
    return _warm(ds, sid)


@app.post("/api/build/upload")
def build_upload(polytope: UploadFile = File(...), n_samples: int = Form(20000)):
    """Build a dataset from an uploaded polytope .npz (technologies + cost, same
    schema as the shipped polytope), generating `n_samples` samples."""
    n = _require_n(n_samples)
    raw = polytope.file.read() if polytope.file else b""
    poly = _load_npz_upload(polytope, "polytope", raw)
    problems = validate_polytope(poly)
    if problems:
        raise HTTPException(status_code=422, detail={"errors": problems})
    try:
        # the uploaded polytope exists nowhere else — store it so the session can
        # be rebuilt after a restart
        sid, ds = sessions.create(poly, source="upload", poly_bytes=raw, n_samples=n,
                                  name=polytope.filename or "uploaded polytope")
    except Exception as e:  # noqa: BLE001 — construction failures → 422 with context
        raise HTTPException(status_code=422, detail=f"failed to build dataset: {e}")
    return _warm(ds, sid)


def _display_subset(n: int, sample: int | None) -> np.ndarray:
    """Row ids to serve for a `sample=N` display request: seeded and sorted, so
    every endpoint honouring `sample` returns the *same* designs — the frontend
    pairs projected points with their raw values positionally."""
    if sample is None or sample >= n:
        return np.arange(n)
    rng = np.random.default_rng(42)
    return np.sort(rng.choice(n, size=int(sample), replace=False))


@app.get("/api/projection")
def projection(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    sample: int | None = Query(None, description="optionally downsample to N points"),
    ds: Dataset = Depends(current_dataset),
):
    try:
        pts, ev, cached, opt, comp = project(ds, method, sampler, dims)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        # 425 Too Early: the expensive projection hasn't been built yet.
        raise HTTPException(status_code=425, detail=str(e))

    index = _display_subset(pts.shape[0], sample)
    if len(index) < pts.shape[0]:
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
    ds: Dataset = Depends(current_dataset),
):
    """Per-sample scalar used to color the scatter, aligned to sample row order."""
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
    sample: int | None = Query(None, description="optionally downsample to N rows"),
    ds: Dataset = Depends(current_dataset),
):
    """Raw per-design values. Pass `sample=N` (same N as `/api/projection`) to get
    the identical seeded subset — the cloud can be 100k designs and serializing all
    of them as JSON is tens of MB the UI never draws."""
    try:
        data = ds.get_samples(sampler, space)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    names = [f.strip() for f in fields.split(",") if f.strip()]
    try:
        cols = [ds.axes.index(n) for n in names]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"unknown field: {e}")

    index = _display_subset(data.shape[0], sample)
    return {
        "fields": names,
        "values": data[np.ix_(index, cols)].astype(float).tolist(),
        "index": index.astype(int).tolist(),
    }


@app.get("/api/extremes")
def extremes(sampler: str = Query("chrrt"), ds: Dataset = Depends(current_dataset)):
    """MGA extreme designs: per technology, its min and max over the polytope
    (LP vertices), as physical values + base-PCA coordinates."""
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


class MarginalsRequest(BaseModel):
    # the axes currently SHOWN — the projection is taken onto exactly these
    axes: list[str]
    n: int = 1500


@app.get("/api/dependence")
def dependence(sampler: str = Query("chrrt"), ds: Dataset = Depends(current_dataset)):
    """Pairwise dependence between the 10 axes: distance correlation, mutual
    information, and Pearson. Distance correlation/MI catch the nonlinear coupling
    that Pearson misses in the uniform near-optimal cloud."""
    return run_dependence(ds, sampler)


@app.get("/api/shadow_pairs")
def shadow_pairs(ds: Dataset = Depends(current_dataset)):
    """All axis pairs ranked by shadow interestingness (ascending boxiness)."""
    return {"pairs": run_shadow_pairs(ds)}


@app.post("/api/shadow")
def shadow(req: ShadowRequest, ds: Dataset = Depends(current_dataset)):
    """Exact 2-D shadow polygon of the (optionally constrained) polytope."""
    try:
        return run_shadow(ds, req.x, req.y, [c.model_dump() for c in req.constraints])
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/flexibility")
def flexibility(req: FlexibilityRequest, ds: Dataset = Depends(current_dataset)):
    """Exact remaining [min, max] per axis under the given constraints."""
    return run_flexibility(ds, [c.model_dump() for c in req.constraints])


@app.post("/api/marginals")
def marginals(req: MarginalsRequest, ds: Dataset = Depends(current_dataset)):
    """Uniform sample of the PROJECTION of the near-optimal body onto the shown
    axes, in physical units — the distribution the Profiles violins draw.

    Reading the full-space cloud along an axis (worldview A) weights each
    combination of the shown axes by how many ways the hidden axes can realize
    it, so hiding an axis leaves every violin identical. This endpoint answers
    the other question (worldview B): over the reachable COMBINATIONS of the
    axes you kept, each counted once. With every axis shown the two coincide and
    the base cloud is returned unchanged."""
    if not req.axes:
        raise HTTPException(status_code=422, detail="axes must be non-empty")
    unknown = [a for a in req.axes if a not in ds.axes]
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown axes: {unknown}")
    n = max(200, min(int(req.n), 4000))
    return run_marginals(ds, req.axes, n=n)


@app.post("/api/generate")
def generate(req: GenerateRequest, ds: Dataset = Depends(current_dataset)):
    """Add the user's constraints to the polytope and re-sample fresh candidate
    designs from the constrained near-optimal region (projected into PCA space)."""
    cons = [c.model_dump() for c in req.constraints]
    return run_generate(ds, req.sampler, req.n, cons, seed=req.seed)


@app.get("/api/clusters")
def clusters(
    method: str = Query("pca"),
    sampler: str = Query("chrrt"),
    dims: int = Query(2, ge=2, le=3),
    k: int = Query(6, ge=2, le=12),
    ds: Dataset = Depends(current_dataset),
):
    """K-means clusters of the projected designs, each characterized by the
    technologies that most distinguish it from the overall mean. Returns centroid
    positions in projection space (so the UI can label them on the scatter)."""
    from sklearn.cluster import KMeans

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
