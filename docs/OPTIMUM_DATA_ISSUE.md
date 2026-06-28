# Data issue: the cost optimum sits outside the sampled near-optimal region

**Status:** open — needs confirmation from whoever produced `polytope_samples.npz`
(`config.source_polytope` / `config.fmax_json`, the ZEN-garden
`Crystal_Ball_2050gf` ORACLE run).

**TL;DR.** The provided cost optimum (`u_star` / `c_star`) maps to the normalized
corner `[1, 1, 1, 1, 1, 1, 1, 1, 1, 0]` — i.e. the **maximum of every one of the
9 technologies and the minimum cost simultaneously**. It is *outside* the sampled
polytope: above the 20,000-sample maximum on **every** technology and below the
sample minimum on cost. This is why, in the app, the optimum's parallel-coordinates
line is a flat line pinned across the top of all technology axes that drops to the
bottom for cost, and why no sampled design resembles it.

This is almost certainly an upstream **normalization / provenance** artifact, not a
bug in this app: the app draws exactly the point it is given.

---

## Evidence

Measured against the 20,000 `chrrt` samples (physical units; `har` is the same
picture). Reproduce with the snippet at the bottom.

### 1. Per-axis percentile of the optimum within the samples

| axis | sample min | sample max | optimum | optimum percentile |
|---|--:|--:|--:|--:|
| nuclear | 0.0 | 110.3 | 110.4 | 100.0% |
| photovoltaics | 142.7 | 6670.0 | 7270.7 | 100.0% |
| wind_offshore | 0.0 | 788.6 | 860.1 | 100.0% |
| wind_onshore | 205.2 | 3254.8 | 3371.3 | 100.0% |
| electrolysis | 0.1 | 1354.3 | 1566.8 | 100.0% |
| DAC | 0.0 | 47.0 | 49.1 | 100.0% |
| battery | 0.1 | 10395.6 | 11357.1 | 100.0% |
| ccs_lump | 0.0 | 409.2 | 454.8 | 100.0% |
| biomass | 114566 | 777882 | 777924 | 100.0% |
| net_present_cost | 1266126 | 1321468 | 1258542 (= c\*) | 0.0% |

The optimum exceeds the sample max on **all 9** technologies and is below the
sample min on cost. Total installed capacity (sum of the 9 techs) at the optimum
is **802,964** — the highest of the entire set (sample mean ≈ 620,479).

### 2. The optimum in normalized coordinates is the unit corner

`Dataset.optimum_norm` evaluates to exactly:

```
[1, 1, 1, 1, 1, 1, 1, 1, 1, 0]   # 9 techs = 1.0, cost = 0.0
```

This is the signature of the upstream scaling: **each technology was normalized by
its own optimum value** (so optimum → 1.0), and **cost was normalized into the
`[c*, 1.05·c*]` band** (so optimum → 0.0). The normalized samples are therefore all
`≤ 1` on each technology and `≥ 0` on cost — i.e. the optimum is the reference
corner the relaxed region hangs *below*.

### 3. The optimum is outside the polytope; the samples are inside

- All 20,000 normalized samples satisfy the polytope `A·x ≤ b` (worst violation
  `-1.5e-6`, i.e. inside). So the normalized frame **is** the polytope frame.
- The optimum violates the polytope by `max(A·x − b) ≈ 2.0` in that same frame —
  a large violation in a roughly `[0,1]` space, not a rounding artifact.

---

## Why this matters / the open question

For a cost-minimization whose feasible set is then relaxed to `cost ≤ 1.05·c*`,
the relaxed region is *larger* than the optimum's neighborhood, so one would expect
samples to be able to **exceed** the optimum on at least some technologies. Instead
the optimum dominates the samples on *every* axis. Two possibilities:

1. **Intended (a modelling fact).** In this 2050 net-zero system the least-cost
   design really does build the maximum of every represented technology, because
   cost trades primarily against something *not* in these 9 axes (e.g. unmet
   demand, imports, or a fossil/emissions penalty in the source model), and any
   cost relaxation buys reductions by substitution. If so, the visualization is
   faithful and we should simply annotate the optimum as the extreme corner.

2. **A provenance mismatch (a data bug).** `u_star` comes from the original
   (un-rounded / higher-resolution) optimization, while the polytope and samples
   come from the PolyRound-rounded relaxation. If the two were normalized against
   slightly different references, `u_star` would land just outside the rounded
   body on every axis — exactly the pattern observed.

**Ask for the data producer:** is `u_star` expressed in the *same* normalization
and polytope as `polytope_samples.npz[*_norm]` / `polytope.npz[A,b]`? If not, we
need either `u_star` in the sampled frame, or the transform that relates them, so
the optimum overlay sits where it physically belongs.

---

## Reproduce

```python
import numpy as np, os; os.environ["DATA_DIR"] = "data"
from app.data import get_dataset          # run from backend/
ds = get_dataset()
S = ds.get_samples("chrrt", "phys")
opt = np.empty(10); opt[ds.tech_idx] = ds.u_star; opt[ds.cost_idx] = ds.c_star
for i, n in enumerate(ds.axes):
    print(f"{n:18} pctile={(S[:,i] < opt[i]).mean()*100:5.1f}%")
print("optimum_norm:", np.round(ds.optimum_norm, 3))
print("polytope violation of optimum:", (ds.A @ ds.optimum_norm - ds.b).max())
print("worst sample violation:", (ds.A @ ds.get_samples('chrrt','norm').T - ds.b[:,None]).max())
```
