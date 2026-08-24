# Workshop demo dataset — "Riverside city plan"

A **synthetic** second dataset, built so the *method* can be taught without
teaching energy modelling first. Pick it on the landing page; it builds in ~3 s at
20 000 designs.

> **The numbers are invented.** Riverside is not a real city and no coefficient
> here is an estimate of anything. Orders of magnitude are plausible so the plot
> axes read sensibly; that is all. The same disclaimer is stored inside the `.npz`.

Source: `backend/scripts/make_demo_polytope.py` (run with `--report` to see the
model, `--write` to regenerate the file).

## The story it tells

A city must hit four statutory targets — mobility, emissions, housing,
resilience — while sharing three scarce resources: civil-works crews, street
space, and grid connections. The LP finds the **cheapest compliant plan**
(`c* = €10.1 bn`). The tool then explores every plan costing at most **+10%** —
i.e. everything a councillor could defend as "within 10% of the cheapest plan
that still meets every target".

Eight programmes, each in its own unit:

| programme | unit | at the optimum | can range over |
|---|---|---|---|
| `heat_mw` | MW | 0 | 0 – 607 |
| `retrofit` | k dwellings | 17.3 | 9.4 – 30 |
| `metro_km` | km | 15.0 | 12.3 – 25.5 |
| `tram_km` | km | 70.0 | 31.7 – 70 |
| `cycle_km` | km | 31.2 | 0 – 79 |
| `bus_fleet` | 100 vehicles | 0 | 0 – 22 |
| `housing` | k dwellings | 7.7 | 4.5 – 11.9 |
| `flood_km` | km | 16.8 | 8.1 – 30 |

## Why this dataset and not the energy one

It exercises parts of the tool the real data never reaches:

| | Swiss energy (v13) | Riverside (demo) |
|---|---|---|
| trade-off | 21 | 7 |
| dependency | 4 | 3 |
| at least one | 1 | **1** |
| band / locked | **0 — never occurs** | **3** |
| independent | 10 | 14 |
| max dCor | 0.33 | **0.90** |

`band` (two programmes substitute one-for-one, their total pinned) simply does not
occur on the energy polytope at ε = 0.1, so the shape taxonomy is only half
demonstrable there. Here it does. The coupling is also far stronger, so the matrix
has real colour range instead of a wall of dark cells.

## Suggested run of show

**1 — the space, not the answer.** Open on Coupling. `metro_km × tram_km` is
0.90 and dashed: they are a *band*, substituting for each other against the
mobility target. Click the cell to see the exact facet.

**2 — a free choice.** Drag the `heat_mw` row down to near zero.
Result: *nothing meaningful is forced.* District heating is optional here — the
emissions target can be met other ways. This is the "you gave something up and
paid almost nothing" case.

**3 — the linchpin.** Clear it, then do the same to `retrofit`.
Result: **housing forced ≥ 8.9k**, **flood defence forced ≥ 20.1 km**, and heat,
metro and housing all take ceilings. Retrofit was quietly carrying three targets
at once — emissions, housing delivery and resilience — and removing it makes the
rest of the plan rigid.

The contrast between 2 and 3 is the whole lesson: *giving something up does not
automatically force anything else — until it does.* The guided tour (`?`) walks
exactly this pair; the dataset's axis order is arranged so it picks them.

**4 — substitutes.** `retrofit × flood_km` is the green *at least one* facet:
resilience has to come from somewhere. You may drop either, never both.

**5 — the Map.** Switch to Map for the 20 000-design cloud in PCA space, and pin
two contrasting plans to compare their radar glyphs side by side.
