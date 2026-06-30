# Data issue: the "optimum" outside the polytope — RESOLVED

**Status:** resolved (2026-06-30). It was a misread of the data, not a bug — in
either the app or the upstream pipeline.

**TL;DR.** The old `u_star` was **never the optimum**. It is the vector of
**per-axis normalization maxima** (each technology axis is normalized by its own
maximum), which is exactly why it maps to the unit corner
`[1, 1, 1, 1, 1, 1, 1, 1, 1, 0]`. The app was overlaying that normalization vector
as if it were the cost optimum, so it looked like a flat line pinned across the top
of every technology axis.

The **real optimum is `z_star`**, added as an explicit key in the v08 data files
(`polytope_08.npz["z_star"]`, 9-D physical technologies; also
`config["z_star_physical"]`). In normalized coordinates it is:

```
z_star_norm = [0.217, 0.273, 0.057, 0.357, 0.106, 0.0, 0.041, 0.073, 1.0, 0.0]
              nuclear  PV   w-off  w-on  electro DAC  batt  ccs  biomass cost
```

and `z_star_norm = z_star_phys / u_star` per technology (e.g. 23.9/110.4 = 0.217;
biomass 777924/777924 = 1.0), confirming `u_star` is the maxima.

## Verification (against `polytope_08`)

- `max(A · z_star_norm − b) = 0.0`, **0 / 429 constraints violated** → the optimum
  is genuinely **inside** the polytope (a proper vertex).
- Per-axis it sits inside the sampled range except where an optimum *should*:
  **cost = 0.0** (it is the cost-minimizer, so below every near-optimal sample),
  **biomass = 1.0** (maxed), **DAC = 0** (floor). All expected.

## What changed in the app

- `backend/app/data.py` now loads the optimum from `z_star` (falls back to
  `u_star` with a warning only for legacy files that lack `z_star`), and keeps the
  real maxima as `self.norm_max`. `self.u_star` is now a back-compat alias holding
  the **optimum** so the `/api/meta` `optimum.u_star` field and the frontend
  "u* optimum" pin show the correct point.
- The loader also resolves **versioned filenames** (`polytope_08.npz`,
  `polytope_samples_08.npz`, …) by picking the newest suffix, since the upstream
  data is delivered versioned.

## Caveats / still open upstream

- The data producer reported a **known flaw in the sampling/optimization algorithm**
  (accuracy of the solutions) that they are fixing; the v08 polytopes are
  provisional. This does not block the visualization work — only how accurate the
  numbers are.
- **Stale projection caches:** `backend/cache/*.npy` (t-SNE/UMAP) were built on the
  old data and the old (wrong) optimum. Rebuild with
  `docker compose exec backend python -m app.projections --force` (PCA is live, so
  the default map is already correct).
