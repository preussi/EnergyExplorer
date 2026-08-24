#!/usr/bin/env python3
"""Build the synthetic *City infrastructure plan* demo polytope.

WHY THIS EXISTS
---------------
The shipped dataset is a real Swiss energy system, which is the point of the tool
but a poor first five minutes of a workshop: nine technologies, GW vs GWh vs
ktCO2/h, and a cost optimum nobody in the room can sanity-check. This file builds
a second dataset in a domain everyone already reasons about — a city deciding what
to build with a fixed capital plan — so the *method* (near-optimal space, coupling,
trade-off facets, flexibility) can be taught without teaching energy modelling
first. The numbers are invented. They are plausible in order of magnitude and
nothing more; see DISCLAIMER below, which is also carried in the file itself.

THE MODEL
---------
Eight programmes, each measured in its own physical unit. A linear programme picks
the cheapest plan that hits every statutory target:

    minimise   c . x            (capital cost, M EUR)
    subject to targets(x) >= t   (mobility, emissions, housing, resilience)
               resources(x) <= r (civil crews, street space, grid connections)
               couplings(x)      (heat network needs retrofit density, etc.)
               0 <= x <= x_max

`c*` is that minimum. The near-optimal body is every plan costing at most
(1 + epsilon) c*  — i.e. every plan a councillor could defend as "within 10% of
the cheapest compliant plan". That body is what the tool explores.

FILE FORMAT (see backend/app/data.py)
-------------------------------------
`A` has one column per programme PLUS a trailing `net_present_cost` column, and
acts on NORMALISED coordinates (phys = norm * u_star, so each axis runs 0..1).
Cost enters as an EPIGRAPH variable t in [0, 1] with 0 = c* and 1 = (1+eps) c*:

    c_norm . x - eps*c* * t <= c*        (t is at least the plan's cost)
    t <= 1 ,  -t <= 0

`data.py:_eliminate` Fourier-Motzkin's t away at load time and is left with
`c_norm . x <= (1+eps) c*` plus every t-free row — the clean 8-D body. Writing it
this way rather than baking the cost cap in directly is what keeps the file in the
same shape as the upstream energy one.

Run:  python scripts/make_demo_polytope.py --report
      python scripts/make_demo_polytope.py --write
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linprog

# --------------------------------------------------------------------------
# DISCLAIMER, also written into the npz so it travels with the data.
DISCLAIMER = (
    "SYNTHETIC DEMO DATA. Riverside is an invented city and every coefficient "
    "here was chosen to make a teachable near-optimal space, not to describe any "
    "real place. Orders of magnitude are plausible; the numbers are not "
    "estimates and must not be cited."
)

EPSILON = 0.10          # near-optimal = within +10% of the cheapest compliant plan

# ---- programmes ----------------------------------------------------------
# name, unit (rendered verbatim by colors.ts:shortUnit), M EUR per unit, build cap
# ORDER MATTERS, and not for the maths. The guided tour has no dataset-specific
# knowledge: it caps axes[0] into its bottom 5% to show "this costs you options
# but forces nothing", then axes[1] to show the opposite. Measured on this body,
# heat_mw is the free choice (forces nothing) and retrofit is the linchpin (forces
# housing +59% and flood defence +55%), so they lead. Everything downstream —
# A's columns, u_star, z_star — is derived from this list, and the rail re-sorts
# by coupling anyway, so the ordering costs nothing else.
PROGRAMMES = [
    ("heat_mw",     "MW",             1.4, 900.0),
    ("retrofit",    "k dwellings",   95.0,  30.0),
    ("metro_km",    "km",           210.0,  26.0),
    ("tram_km",     "km",            42.0,  70.0),
    ("cycle_km",    "km",             1.8, 120.0),
    ("bus_fleet",   "100 vehicles",  65.0,  22.0),
    ("housing",     "k dwellings",  240.0,  20.0),
    ("flood_km",    "km",            28.0,  30.0),
]
NAMES = [p[0] for p in PROGRAMMES]
UNITS = [p[1] for p in PROGRAMMES]
COST = np.array([p[2] for p in PROGRAMMES], dtype=float)
XMAX = np.array([p[3] for p in PROGRAMMES], dtype=float)
N = len(PROGRAMMES)
IDX = {n: i for i, n in enumerate(NAMES)}


def _row(**kw) -> np.ndarray:
    r = np.zeros(N)
    for k, v in kw.items():
        r[IDX[k]] = v
    return r


# ---- the constraint set --------------------------------------------------
# Each entry is (label, row, sense, rhs) in PHYSICAL units, `sense` in {"<=", ">="}.
# The labels are what the workshop talks about, so they are kept next to the maths.
CONSTRAINTS: list[tuple[str, np.ndarray, str, float]] = [
    # --- statutory targets (lower bounds) --------------------------------
    ("mobility: 300 M trips/yr",
     _row(metro_km=9.0, tram_km=2.2, cycle_km=0.35, bus_fleet=1.1), ">=", 300.0),
    ("emissions: -120 ktCO2/yr",
     _row(metro_km=1.6, tram_km=0.55, cycle_km=0.12, bus_fleet=0.9,
          retrofit=3.1, heat_mw=0.045), ">=", 120.0),
    ("housing: 12k homes delivered",
     _row(housing=1.0, retrofit=0.25), ">=", 12.0),
    # Deliberately SUBSTITUTABLE: hard defences and absorbent retrofitted stock
    # both count, and either can carry the target alone. That is what produces an
    # "at least one" facet — a category the real energy dataset never shows.
    ("resilience: 46 index points",
     _row(flood_km=1.6, retrofit=1.1), ">=", 46.0),

    # --- shared resources (upper bounds) ---------------------------------
    # The trade-off engine: tunnelling, bridge and embankment work all come out
    # of the same pool of civil crews, so anything big crowds out everything big.
    ("civil crews: 205 crew-years",
     _row(metro_km=4.5, tram_km=1.2, cycle_km=0.15, flood_km=0.9, heat_mw=0.035),
     "<=", 205.0),
    # Surface street space: metro is bored, so it does not compete here.
    ("street space: 95 km of corridor",
     _row(tram_km=1.0, cycle_km=0.8), "<=", 95.0),
    # Electrification all lands on the same substation upgrades.
    ("grid connections: 55 MVA",
     _row(bus_fleet=0.9, heat_mw=0.03, retrofit=0.4), "<=", 55.0),

    # --- couplings (what makes the facets interesting) -------------------
    # A heat network only pays where the stock has been retrofitted: heat at
    # scale REQUIRES retrofit. Shows up as a directional dependency facet.
    # Slope chosen so the cap actually BINDS inside the near-optimal body: at 40
    # MW per thousand retrofits it sat above the cost-driven ceiling and never
    # cut anything, so the pair read as an ordinary trade-off.
    ("heat network needs retrofit density",
     _row(heat_mw=1.0, retrofit=-22.0), "<=", 0.0),
    # A metro with no surface feeders strands riders at the portals.
    ("metro needs feeder network",
     _row(metro_km=1.0, tram_km=-0.5), "<=", 4.0),
]


def build_physical() -> tuple[np.ndarray, np.ndarray]:
    """Constraint system in physical units, as A_phys x <= b_phys (incl. bounds)."""
    rows, rhs = [], []
    for _lbl, r, sense, v in CONSTRAINTS:
        if sense == "<=":
            rows.append(r.copy()); rhs.append(v)
        else:
            rows.append(-r); rhs.append(-v)
    for i in range(N):                       # 0 <= x_i <= x_max
        u = np.zeros(N); u[i] = 1.0
        rows.append(u.copy()); rhs.append(XMAX[i])
        rows.append(-u); rhs.append(0.0)
    return np.asarray(rows, float), np.asarray(rhs, float)


def solve(A, b, c, maximise=False):
    res = linprog(-c if maximise else c, A_ub=A, b_ub=b,
                  bounds=[(None, None)] * A.shape[1], method="highs")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write the npz")
    ap.add_argument("--out", default="data/polytope_0_infrastructure_demo.npz")
    args = ap.parse_args()

    A_phys, b_phys = build_physical()

    # 1. the cost optimum
    opt = solve(A_phys, b_phys, COST)
    if not opt.success:
        raise SystemExit(f"infeasible base problem: {opt.message}")
    z_star = opt.x.copy()
    c_star = float(COST @ z_star)

    # 2. the near-optimal body, still physical: add c.x <= (1+eps) c*
    A_near = np.vstack([A_phys, COST])
    b_near = np.concatenate([b_phys, [(1.0 + EPSILON) * c_star]])

    # 3. per-axis maxima over that body -> the fmax normalisation (phys = norm * u*)
    u_star = np.empty(N)
    x_min = np.empty(N)
    for i in range(N):
        e = np.zeros(N); e[i] = 1.0
        hi = solve(A_near, b_near, e, maximise=True)
        lo = solve(A_near, b_near, e)
        if not (hi.success and lo.success):
            raise SystemExit(f"axis {NAMES[i]} unbounded/infeasible")
        u_star[i] = float(hi.x[i]); x_min[i] = float(lo.x[i])
    if np.any(u_star <= 0):
        raise SystemExit(f"axes with zero maximum: "
                         f"{[NAMES[i] for i in np.where(u_star <= 0)[0]]}")

    # 4. rescale into normalised coordinates and append the cost epigraph column
    A_norm = A_phys * u_star[None, :]
    c_norm = COST * u_star
    m = A_norm.shape[0]
    A10 = np.zeros((m + 3, N + 1))
    b10 = np.zeros(m + 3)
    A10[:m, :N] = A_norm
    b10[:m] = b_phys
    A10[m, :N] = c_norm; A10[m, N] = -EPSILON * c_star; b10[m] = c_star  # epigraph
    A10[m + 1, N] = 1.0;  b10[m + 1] = 1.0                                # t <= 1
    A10[m + 2, N] = -1.0; b10[m + 2] = 0.0                                # t >= 0

    axis_meta = {"axes": [{"name": n, "members": [n], "unit": u}
                          for n, u in zip(NAMES, UNITS)]}

    # ---- report -----------------------------------------------------------
    print(f"cost optimum c*  = {c_star:,.0f} MEUR   (epsilon = {EPSILON})")
    print(f"near-optimal cap = {(1+EPSILON)*c_star:,.0f} MEUR")
    print(f"{'programme':<12}{'unit':>14}{'optimum':>12}{'min':>10}{'max':>12}"
          f"{'headroom':>10}")
    for i, n in enumerate(NAMES):
        span = u_star[i] - x_min[i]
        print(f"{n:<12}{UNITS[i]:>14}{z_star[i]:>12.1f}{x_min[i]:>10.1f}"
              f"{u_star[i]:>12.1f}{span/max(u_star[i],1e-9)*100:>9.0f}%")
    binding = [lbl for (lbl, r, sense, v) in CONSTRAINTS
               if abs((r @ z_star) - v) < 1e-6 * max(1.0, abs(v))]
    print("binding at the optimum:", "; ".join(binding) or "(none)")

    if args.write:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            out,
            A=A10, b=b10,
            name_list=np.array(NAMES + ["net_present_cost"]),
            units=np.array(UNITS + ["megaEuro"]),
            u_star=u_star, z_star=z_star,
            c_star=np.array(c_star), epsilon=np.array(EPSILON),
            cost_axis=np.array("net_present_cost"),
            axis_meta_json=np.array(json.dumps(axis_meta)),
            disclaimer=np.array(DISCLAIMER),
        )
        print(f"\nwrote {out}  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
