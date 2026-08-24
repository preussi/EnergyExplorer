<script lang="ts">
  import {
    getMeta, getProjection, getColor, getSamples, getClusters, getExtremes, generate,
    getFlexibility, getDependence, getShadowPairs, getMarginals,
    type Meta, type Projection, type ColorData, type SamplesData, type ClustersData,
    type GenerateResult, type ConstraintInput, type ExtremeDesign, type FlexRange,
    type Dependence, type DependenceMetric, type ShadowPair, type Marginals,
    setDatasetId, storedDatasetId, UnknownDataset,
  } from "./lib/api";
  import { onMount } from "svelte";
  import { fly } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
  import Landing from "./lib/Landing.svelte";
  import ScatterGL from "./lib/ScatterGL.svelte";
  import ParallelCoords from "./lib/ParallelCoords.svelte";
  import RadarGlyph from "./lib/RadarGlyph.svelte";
  import FacetView from "./lib/FacetView.svelte";
  import ConsequenceStrip from "./lib/ConsequenceStrip.svelte";
  import StarWheel from "./lib/StarWheel.svelte";
  import DependenceMatrix from "./lib/DependenceMatrix.svelte";
  import Tour from "./lib/Tour.svelte";
  import { clusterOrder } from "./lib/cluster";
  import { markGradient, pinColor, PIN_SLOTS, setColorTheme, shortUnit } from "./lib/colors";
  import { getShadow, type CornerGaps } from "./lib/api";
  import { CAT, classifyFacet, GAP_EPS_DEFAULT } from "./lib/facets";

  let meta = $state<Meta | null>(null);
  let proj = $state<Projection | null>(null);
  let colorData = $state<ColorData | null>(null);
  let samples = $state<SamplesData | null>(null);
  let clustersData = $state<ClustersData | null>(null);
  let extremesData = $state<ExtremeDesign[]>([]);

  // Cap how many designs are rendered. The 20k full set is visually overwhelming;
  // the backend downsamples deterministically (seeded), and color / parallel-coords
  // / normalized-tech are all gathered through proj.index so the views stay aligned.
  const DISPLAY_N = 50000;

  let method = $state("pca");
  const sampler = "chrrt"; // sampler choice removed from UI; chrrt is the default view
  let field = $state(""); // "" = no color encoding (default)
  let showOptimum = $state(true);
  let showClusters = $state(true);

  // visual-analytics overlays
  let showDensity = $state(true);
  let showCompass = $state(false);
  let showSpokes = $state(false);
  let walkOn = $state(false);

  let loading = $state(false);
  let error = $state<string | null>(null);
  let scatter = $state<ScatterGL>();
  let pcoords = $state<ParallelCoords>();

  // hover state
  let hoverIdx = $state<number | null>(null);
  let mouse = $state<[number, number]>([0, 0]);

  // linked selection (row indices; identity between scatter & parallel coords)
  let selected = $state<number[] | null>(null);
  let mode = $state<"pan" | "select">("pan");

  // ---- view mode: the coupling matrix vs the projection map ----
  // The exact facet view has no tab of its own: it is reached by clicking a cell
  // in the Coupling matrix, which docks it beside the matrix (and ⛶ maximizes it).
  // Parallel coords have no tab either — they are the bottom strip in both views.
  let viewMode = $state<"map" | "coupling">("coupling");
  // pairwise dependence (dCor / MI / Pearson) for the Coupling matrix
  let depData = $state<Dependence | null>(null);
  let depMetric = $state<DependenceMetric>("dcor");
  let shadowPairs = $state<ShadowPair[]>([]);   // boxiness per pair (facet fallback order)
  // a pair to draw in the facet panel (set by clicking a heatmap cell); one-shot,
  // FacetView consumes and clears it (see onconsumepair).
  let facetSel = $state<{ x: string; y: string } | null>(null);
  // whether the Coupling view's docked Facet panel is open (persists across the
  // one-shot facetSel so re-clicking cells doesn't close it). null = closed.
  let couplingPair = $state<{ x: string; y: string } | null>(null);
  // ⛶ full view: the docked facet takes the whole coupling region (matrix hidden)
  let couplingFull = $state(false);

  // ---- star coordinates (user-steered linear projection) ----
  const isStar = $derived(method === "star");
  let normTech = $state<number[][] | null>(null); // (≤ DISPLAY_N) x 9, normalized tech values
  let starAnchors = $state<[number, number][]>([]);
  let touring = $state(false);
  let tourTimer: ReturnType<typeof setInterval> | null = null;
  let tourTarget: [number, number][] = [];

  async function ensureNormTech() {
    if (normTech || !meta) return;
    const s = sampler; // discard the response if the sampler changed mid-flight
    const tech = meta.axes; // all 9 axes are technologies
    try {
      const r = await getSamples(s, tech, "norm", DISPLAY_N);
      if (s === sampler) normTech = r.values;
    } catch (e) { console.warn("normTech load failed:", e); }
  }
  function pcaAnchors(): [number, number][] {
    if (!proj?.components) return circleAnchors();
    const c = proj.components;
    const maxN = Math.max(...c[0].map((_, j) => Math.hypot(c[0][j], c[1][j]))) || 1;
    return c[0].map((_, j) => [(c[0][j] / maxN) * 0.85, (c[1][j] / maxN) * 0.85]);
  }
  function circleAnchors(): [number, number][] {
    const n = (meta?.axes.length ?? 10) - 1;
    return Array.from({ length: n }, (_, j) => {
      const a = (j / n) * 2 * Math.PI;
      return [0.7 * Math.cos(a), 0.7 * Math.sin(a)] as [number, number];
    });
  }
  function randomFrame(): [number, number][] {
    const n = starAnchors.length || 9;
    const u = Array.from({ length: n }, () => Math.random() * 2 - 1);
    const v = Array.from({ length: n }, () => Math.random() * 2 - 1);
    const nu = Math.hypot(...u) || 1;
    const un = u.map((x) => x / nu);
    const dot = v.reduce((s, x, j) => s + x * un[j], 0);
    const vo = v.map((x, j) => x - dot * un[j]);
    const nv = Math.hypot(...vo) || 1;
    const vn = vo.map((x) => x / nv);
    const maxN = Math.max(...un.map((_, j) => Math.hypot(un[j], vn[j]))) || 1;
    return un.map((_, j) => [(un[j] / maxN) * 0.85, (vn[j] / maxN) * 0.85]);
  }
  // Gram-Schmidt the two columns (x-/y-components across axes) of the anchor
  // frame back to orthonormal, then rescale to a 0.85 radius. Keeps every toured
  // frame *area-true* (a real grand tour) instead of squishing through the linear
  // interpolation between two orthonormal frames.
  function orthonormalizeAnchors(anchors: [number, number][]): [number, number][] {
    const n = anchors.length;
    let f1 = anchors.map((a) => a[0]);
    let f2 = anchors.map((a) => a[1]);
    const dot = (u: number[], v: number[]) => u.reduce((s, x, j) => s + x * v[j], 0);
    const nrm = (v: number[]) => Math.sqrt(dot(v, v)) || 1;
    f1 = f1.map((x) => x / nrm(f1));
    const d = dot(f2, f1);
    f2 = f2.map((x, j) => x - d * f1[j]);
    f2 = f2.map((x) => x / nrm(f2));
    const anc = Array.from({ length: n }, (_, j) => [f1[j], f2[j]] as [number, number]);
    const maxN = Math.max(...anc.map((a) => Math.hypot(a[0], a[1]))) || 1;
    return anc.map((a) => [(a[0] / maxN) * 0.85, (a[1] / maxN) * 0.85] as [number, number]);
  }
  function toggleTour() {
    if (touring) {
      touring = false;
      if (tourTimer) { clearInterval(tourTimer); tourTimer = null; }
      return;
    }
    touring = true;
    tourTarget = randomFrame();
    tourTimer = setInterval(() => {
      const lerped = starAnchors.map((a, j) => {
        const t = tourTarget[j] ?? a;
        return [a[0] + (t[0] - a[0]) * 0.045, a[1] + (t[1] - a[1]) * 0.045] as [number, number];
      });
      starAnchors = orthonormalizeAnchors(lerped);
      let maxd = 0;
      for (let j = 0; j < starAnchors.length; j++) {
        const t = tourTarget[j] ?? starAnchors[j];
        maxd = Math.max(maxd, Math.hypot(t[0] - starAnchors[j][0], t[1] - starAnchors[j][1]));
      }
      if (maxd < 0.02) tourTarget = randomFrame();
    }, 50);
  }
  // Direct manipulation: drag a design (a pin or the optimum) and rotate the
  // whole projection so that design moves toward the cursor — the Grand Tour's
  // "data-point mode". We nudge each frame column by a multiple of the dragged
  // design's tech vector, then re-orthonormalize (keeps it area-true).
  function rotateFrameToward(t: number[], qx: number, qy: number) {
    if (!starAnchors.length) return;
    const tn = Math.hypot(...t);
    if (tn < 1e-6) return;
    const tu = t.map((x) => x / tn);
    let f1 = starAnchors.map((a) => a[0]);
    let f2 = starAnchors.map((a) => a[1]);
    const cur1 = f1.reduce((s, x, j) => s + x * t[j], 0);
    const cur2 = f2.reduce((s, x, j) => s + x * t[j], 0);
    const DAMP = 0.5; // ease toward the cursor so the drag feels smooth
    const a = ((cur1 + (qx - cur1) * DAMP) - cur1) / tn;
    const b = ((cur2 + (qy - cur2) * DAMP) - cur2) / tn;
    f1 = f1.map((x, j) => x + a * tu[j]);
    f2 = f2.map((x, j) => x + b * tu[j]);
    starAnchors = orthonormalizeAnchors(
      Array.from({ length: f1.length }, (_, j) => [f1[j], f2[j]] as [number, number]),
    );
  }
  function onMarkerDrag(kind: string, id: number, qx: number, qy: number) {
    if (!isStar || !starAnchors.length || !meta) return;
    if (touring) toggleTour(); // a manual grab pauses the auto-tour
    let t: number[] | null = null;
    if (kind === "optimum") {
      t = meta.optimum.norm; // 9-D, technologies only
    } else {
      t = pins.find((p) => p.id === id)?.normTech ?? null;
    }
    if (t) rotateFrameToward(t, qx, qy);
  }
  // initialize anchors the first time star mode is ready (circle layout: every
  // handle is grabbable; PCA loadings would bunch most anchors at the center)
  $effect(() => {
    if (isStar && starAnchors.length === 0 && meta) {
      starAnchors = circleAnchors();
    }
  });
  const starPoints = $derived.by(() => {
    if (!isStar || !dispNormTech || !starAnchors.length) return [];
    const A = starAnchors;
    return dispNormTech.map((row) => {
      let x = 0, y = 0;
      for (let j = 0; j < A.length; j++) { x += row[j] * A[j][0]; y += row[j] * A[j][1]; }
      return [x, y];
    });
  });
  const starOptimum = $derived.by(() => {
    if (!isStar || !meta || !starAnchors.length) return null;
    const tech = meta.optimum.norm; // 9-D, technologies only
    let x = 0, y = 0;
    for (let j = 0; j < starAnchors.length; j++) {
      x += tech[j] * starAnchors[j][0];
      y += tech[j] * starAnchors[j][1];
    }
    return [x, y];
  });

  // ---- remaining flexibility (exact LP ranges under current constraints) ----
  let flexBase = $state<FlexRange[]>([]);
  let flexCur = $state<FlexRange[]>([]);
  let flexFeasible = $state(true);
  let flexBusy = $state(false);
  let flexTimer: ReturnType<typeof setTimeout> | null = null;

  // ---- pinned designs & A→B morphing ----
  interface Pin {
    id: number;
    label: string;
    values: number[]; // physical, all 10 axes
    point: [number, number]; // projection coords at pin time
    normTech?: number[]; // normalized tech values (lets star mode re-project live)
    /** palette SLOT, not a resolved colour: the pin palette is theme-dependent
     *  (dark pastels are 1.2-1.6:1 on white), and a colour baked in at pin time
     *  would keep the old theme's value after a flip. */
    slot: number;
  }
  let pins = $state<Pin[]>([]);
  let pinSeq = 0;
  let morphT = $state(0);
  let morphPlaying = $state(false);
  let morphTimer: ReturnType<typeof setInterval> | null = null;
  let hoverPin = $state<number | null>(null); // index into pins
  let simPin = $state<number | null>(null); // pin id whose "find similar" highlight is active

  function addPin(label: string, values: number[], point: [number, number], normVals?: number[]) {
    if (pins.length >= 6) return;
    if (pins.some((p) => p.label === label)) return;
    // Pick the first palette color not already in use, so removing a pin and
    // adding another doesn't collide with a surviving pin's color.
    const used = new Set(pins.map((p) => p.slot));
    let slot = 0;
    while (slot < PIN_SLOTS && used.has(slot)) slot++;
    if (slot >= PIN_SLOTS) slot = pins.length % PIN_SLOTS;
    pins = [...pins, {
      id: pinSeq++, label, values, point, normTech: normVals, slot,
    }];
    morphT = 0;
  }
  function pinRow(i: number) {
    if (!dispValues[i] || !dispPoints[i]) return;
    addPin(
      `${inCandidates ? "cand" : "#"}${i}`,
      dispValues[i],
      dispPoints[i] as [number, number],
      !inCandidates ? dispNormTech?.[i] : undefined,
    );
  }
  function pinOptimum() {
    if (!dispOptimum || !meta) return;
    addPin("optimum", [...meta.optimum.u_star], dispOptimum as [number, number],
      meta.optimum.norm);
  }
  // pins re-projected live in star mode (anchors move under them)
  function starProject(t: number[]): [number, number] {
    let x = 0, y = 0;
    for (let j = 0; j < starAnchors.length; j++) { x += t[j] * starAnchors[j][0]; y += t[j] * starAnchors[j][1]; }
    return [x, y];
  }
  const pinColors = $derived(pins.map((p) => pinColor(p.slot, theme)));
  const pinPoints = $derived(
    pins.map((p) =>
      isStar && p.normTech && starAnchors.length ? starProject(p.normTech) : p.point,
    ),
  );
  function pinExtreme(key: string) {
    const e = extremesData.find((x) => `${x.kind}-${x.axis}` === key);
    if (!e) return;
    addPin(`${e.kind} ${short(e.axis)}`, e.values, e.point as [number, number]);
  }
  function removePin(i: number) {
    pins = pins.filter((_, j) => j !== i);
    stopMorph(); morphT = 0; hoverPin = null;
  }
  function stopMorph() {
    morphPlaying = false;
    if (morphTimer) { clearInterval(morphTimer); morphTimer = null; }
  }
  function toggleMorph() {
    if (morphPlaying) { stopMorph(); return; }
    if (morphT >= 1) morphT = 0;
    morphPlaying = true;
    morphTimer = setInterval(() => {
      morphT = Math.min(1, morphT + 0.012);
      if (morphT >= 1) stopMorph();
    }, 30);
  }
  const lerp = (a: number[], b: number[], t: number) => a.map((v, i) => v + (b[i] - v) * t);

  // highlight ~250 designs most similar to a pin (normalized distance over technologies)
  function findSimilar(p: Pin) {
    if (simPin === p.id) { clearSelection(); return; } // clicking again clears it
    if (!dispValues.length || !techRanges.length) return;
    clearBrushConstraints();
    const tech = dispFields.map((_, j) => j).filter((j) => dispFields[j] !== "net_present_cost");
    const span = tech.map((j) => {
      const rg = techRanges[techIdxOf(j)] ?? { min: 0, max: 1 };
      return rg.max - rg.min || 1;
    });
    const d = dispValues.map((row, r) => {
      let s = 0;
      tech.forEach((j, k) => {
        const dv = (row[j] - p.values[j]) / span[k];
        s += dv * dv;
      });
      return [s, r] as [number, number];
    });
    d.sort((a, b) => a[0] - b[0]);
    selected = d.slice(0, 250).map((x) => x[1]);
    simPin = p.id;
  }
  function techIdxOf(fieldIdx: number): number {
    // maps a dispFields index to the 0..8 technology index (cost excluded)
    let k = 0;
    for (let j = 0; j < dispFields.length; j++) {
      if (dispFields[j] === "net_present_cost") continue;
      if (j === fieldIdx) return k;
      k++;
    }
    return -1;
  }

  function clearSelection() {
    selected = null;
    simPin = null;
    // clear the constraints here too: clearBrushes only does it via its callback,
    // which never fires if the parallel coords aren't mounted.
    clearBrushConstraints();
    pcoords?.clearBrushes();
  }

  // ---- Phase 3: steering / generation ----
  let candidates = $state<GenerateResult | null>(null);
  let generating = $state(false);
  let genMsg = $state<string | null>(null);
  // Constraints come from ONE place: dragging along a row. A separate typed
  // min/max entry used to exist alongside brushing and the two never composed
  // legibly — a typed limit and a brush on the same axis both narrowed it with
  // no way to see which did what. Brushing is the whole vocabulary now.
  let brushConstraints = $state<{ axis: string; min: number; max: number }[]>([]);
  let genN = $state(2000);

  const allConstraints = $derived<ConstraintInput[]>(brushConstraints);

  function clearBrushConstraints() { brushConstraints = []; }

  // refresh the exact remaining-flexibility ranges when constraints change.
  // Debounced + sequence-guarded: clearTimeout can't cancel an in-flight fetch,
  // so an out-of-order response must not clobber newer state.
  let flexSeq = 0;
  $effect(() => {
    const cons = allConstraints;
    const my = ++flexSeq; // invalidates any in-flight response, incl. on clear
    if (!cons.length) {
      flexCur = flexBase; flexFeasible = true; flexBusy = false;
      return;
    }
    flexBusy = true;
    flexTimer = setTimeout(async () => {
      try {
        const r = await getFlexibility(cons);
        if (my !== flexSeq) return;
        flexFeasible = r.feasible;
        flexCur = r.feasible ? r.ranges : flexCur;
      } catch { /* keep last */ }
      finally { if (my === flexSeq) flexBusy = false; }
    }, 350);
    return () => { if (flexTimer) clearTimeout(flexTimer); };
  });

  async function generateNow() {
    if (!proj) return;
    generating = true; genMsg = null;
    try {
      if (method !== "pca") {
        // same teardown as a manual method switch: pins/tour belong to the old space
        if (touring) toggleTour();
        pins = []; stopMorph(); morphT = 0;
        method = "pca";
        await loadProjection(); loadClusters();
      }
      const res = await generate({ sampler, n: genN, constraints: allConstraints });
      if (!res.feasible || res.n === 0) {
        candidates = null;
        genMsg = "No feasible designs under these constraints — try relaxing them.";
      } else {
        candidates = res;
        selected = null;
      }
    } catch (e) {
      genMsg = (e as Error).message;
    } finally {
      generating = false;
    }
  }
  function backToFull() {
    candidates = null; genMsg = null; selected = null;
    pcoords?.clearBrushes();
  }

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;

  // star mode is computed client-side on top of the PCA fetch (for loadings/optimum)
  const fetchMethod = $derived(method === "star" ? "pca" : method);

  async function loadProjection() {
    loading = true; error = null;
    try { proj = await getProjection(fetchMethod, sampler, 2, DISPLAY_N); }
    catch (e) { error = (e as Error).message; proj = null; }
    finally { loading = false; }
  }
  async function loadColor() {
    if (!field) { colorData = null; return; } // no encoding → uniform color everywhere
    // Color is always fetched in physical units. (Color is min-max normalized,
    // and phys = scale*norm + offset per axis, so normalized vs physical produce
    // identical colors — only the legend's numeric bounds would differ. Physical
    // shows real magnitudes, so there's no separate unit toggle.)
    // A secondary fetch: a transient failure shouldn't blank the whole view with
    // a blocking error (the scatter still renders). Only the projection/meta do.
    try { colorData = await getColor(sampler, field, "phys"); }
    catch (e) { console.warn("color load failed:", e); }
  }
  async function loadSamples() {
    if (!meta) return;
    try { samples = await getSamples(sampler, meta.axes, "phys", DISPLAY_N); }
    catch (e) { console.warn("samples load failed:", e); }
  }
  async function loadClusters() {
    try { clustersData = await getClusters(fetchMethod, sampler, clusterK); }
    catch { clustersData = null; }
  }
  async function loadExtremes() {
    try { extremesData = (await getExtremes(sampler)).extremes; }
    catch { extremesData = []; }
  }
  async function loadDependence() {
    const s = sampler;
    try { const r = await getDependence(s); if (s === sampler) depData = r; }
    catch { depData = null; }
  }

  function onMethodChange() {
    selected = null;
    candidates = null; genMsg = null;
    pins = []; stopMorph(); morphT = 0; // pin coords live in the previous projection space
    if (touring) toggleTour();
    normTech = null; starAnchors = []; // projection changed; refetch/reinit lazily
    if (method === "star") ensureNormTech();
    loadProjection(); loadColor(); loadSamples(); loadClusters(); loadExtremes();
  }

  // dependence depends only on the sampler — (re)load when it changes
  $effect(() => {
    sampler;
    if (meta) loadDependence();
  });
  // shadow-pair boxiness is sampler-independent (static polytope) — fetch once
  $effect(() => {
    if (meta && !shadowPairs.length) getShadowPairs().then((r) => (shadowPairs = r.pairs)).catch(() => {});
  });

  const clusterMarkers = $derived(
    (clustersData?.clusters ?? []).map((c) => {
      const lab = (z: number, n: string) => `${z >= 0 ? "↑" : "↓"}${short(n)}`;
      const [t1, t2] = c.top;
      const label =
        lab(t1.z, t1.name) +
        (t2 && Math.abs(t2.z) > 0.5 ? " " + lab(t2.z, t2.name) : "");
      return { x: c.x, y: c.y, label, count: c.count };
    }),
  );

  // stop intervals when the component is destroyed (incl. HMR replacement)
  $effect(() => {
    return () => {
      stopMorph();
      if (tourTimer) clearInterval(tourTimer);
    };
  });

  // (Re)load everything for the currently-active backend dataset. Called once on
  // mount and again after a dataset upload/reset, so it also tears down interaction
  // state that belonged to the previous dataset (pins, tour, constraints…).
  async function reloadAll() {
    selected = null; candidates = null; genMsg = null;
    pins = []; stopMorph(); morphT = 0;
    if (touring) toggleTour();
    normTech = null; starAnchors = [];
    brushConstraints = [];
    facetSel = null; couplingPair = null; couplingFull = false;
    shadowPairs = []; depData = null; // force the dependence/shadow-pair effects to refetch
    try {
      const m = await getMeta();
      meta = m;
      dimEnabled = Object.fromEntries(m.axes.map((a) => [a, true])); // all on for a new dataset
      // an uploaded dataset may not carry the previously-selected axis names
      // ("" is the valid no-encoding choice, so leave it alone)
      if (field && !m.axes.includes(field)) field = m.axes[0];
      loadProjection(); loadColor(); loadSamples(); loadClusters(); loadExtremes();
      const r = await getFlexibility([]);
      flexBase = r.ranges;
      // don't clobber a constrained result that may have landed meanwhile
      if (!allConstraints.length) flexCur = r.ranges;
    } catch (e) {
      error = (e as Error).message;
    }
  }
  // Landing gate: the tool boots only after the landing page has built a dataset
  // (generated its samples + precomputed views). `onready` hands us the fresh meta;
  // reloadAll() then fetches the (now cache-warm) views. "change dataset" flips this
  // back to false to return to the landing page.
  let started = $state(false);
  let restoring = $state(true);   // checking for a previous session before deciding

  // The session id is the whole persistence story: the dataset lives on the
  // backend keyed by it, so remembering the id is enough to come back to the same
  // cloud after a refresh — and putting it in the URL makes that link shareable.
  function adoptSession(m: Meta) {
    const id = m.dataset.id;
    setDatasetId(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set("ds", id);
    else url.searchParams.delete("ds");
    history.replaceState(null, "", url);
  }

  function onLandingReady(m: Meta) {
    resumeMeta = null;          // the old session is no longer what we'd go back to
    adoptSession(m);
    meta = m;
    started = true;
    reloadAll();
  }

  // Going back to the landing page must not destroy the session. It used to call
  // setDatasetId(null) and clear ?ds= immediately, which made "change dataset" a
  // ONE-WAY DOOR: the id was the only handle on the built cloud, so backing out
  // meant regenerating it. Now the id is left alone until a new dataset is
  // actually built (`adoptSession` overwrites it), and the landing page offers a
  // way back for as long as `resumeMeta` holds the session we stepped away from.
  let resumeMeta = $state<Meta | null>(null);
  function changeDataset() {
    resumeMeta = meta;
    started = false;
  }
  function resumeSession() {
    if (!resumeMeta) return;
    meta = resumeMeta;
    resumeMeta = null;
    started = true;
  }

  // Boot: if this browser (or the link that was opened) names a dataset, go
  // straight back into it instead of the landing page. A 404 means the session is
  // gone — forget it and let the user build a new one.
  onMount(async () => {
    const id = storedDatasetId();
    if (!id) { restoring = false; return; }
    setDatasetId(id);
    try {
      const m = await getMeta();
      adoptSession(m);
      meta = m;
      started = true;
      reloadAll();
    } catch (e) {
      if (!(e instanceof UnknownDataset)) console.warn("session restore failed:", e);
      setDatasetId(null);
      const url = new URL(location.href);
      url.searchParams.delete("ds");
      history.replaceState(null, "", url);
    } finally {
      restoring = false;
    }
  });

  // ---- guided tour ----
  // Walks a concrete question ("what if we build no offshore wind?") rather than
  // labelling controls in the abstract: each step sets the app up for what it is
  // about to explain, so the user watches the real views react.
  let tourOpen = $state(false);
  let tourStep = $state(0);
  const TOUR_SEEN = "ee.tourSeen";

  // The scenario needs a lever to rule out and one to contrast it against. Prefer
  // the wind pair (the interesting case in the shipped polytope: capping offshore
  // wind forces nothing, capping onshore wind pushes most other levers), but fall
  // back to whatever axes an uploaded polytope actually has.
  const demoAxis = $derived(
    (meta?.axes ?? []).includes("wind_offshore") ? "wind_offshore" : (meta?.axes ?? [])[0] ?? "",
  );
  const demoAxis2 = $derived(
    (meta?.axes ?? []).includes("wind_onshore") ? "wind_onshore"
      : (meta?.axes ?? []).find((a) => a !== demoAxis) ?? "",
  );
  /** cap `axis` at a small share of its own feasible maximum ≈ "essentially none".
   *  Drives a real brush on the row, so the demo shows the same thing the user
   *  would do by hand rather than a constraint materialising invisibly. */
  function capLever(axis: string, share = 0.05) {
    const r = flexBase.find((f) => f.axis === axis);
    if (!r || r.max == null || r.min == null) return;
    pcoords?.setBrush(axis, r.min, +(r.max * share).toPrecision(2));
  }
  function clearScenario() {
    brushConstraints = [];
    pcoords?.clearBrushes();
  }

  const tourSteps = $derived([
    {
      title: "A space of answers, not one answer",
      body: [
        `Every design in here costs within a few % of the cost-optimal system, so none of them is "wrong". The tool is for seeing what that freedom actually buys you.`,
        `This guide walks one question end to end: what happens if we build essentially no ${short(demoAxis)}?`,
      ],
      next: "Start",
      setup: "reset",
    },
    {
      target: '[data-tour="views"]',
      title: "Two views",
      body: [
        "Coupling shows how the levers constrain each other. Map is a PCA projection of the design cloud.",
        "The exact trade-off boundaries live inside Coupling — click a cell to open one.",
      ],
      setup: "reset",
    },
    {
      target: '[data-tour="matrix"]',
      title: "Which levers move together",
      body: [
        "Rows and columns are technologies, ordered by hierarchical clustering so the strongly-coupled ones sit next to each other.",
        "Lower-left is the dependence value; upper-right is that pair's exact 2-D boundary. Click any cell to open it full size.",
      ],
      setup: "reset",
    },
    {
      target: '[data-tour="full-view"]',
      title: "The exact trade-off boundary",
      body: [
        `This is the true ${short(demoAxis)} × ${short(demoAxis2)} facet. The dots are sampled designs, but the white outline around them is exact — computed by linear programs on the polytope, not traced from the dots.`,
        "⛶ full view expands it over the matrix; ✕ closes it.",
      ],
      setup: "facet",
    },
    {
      target: '[data-tour="pcoords"]',
      title: "Every lever at once",
      body: [
        "Each horizontal row is one technology; the violin is how the designs are distributed along it, and the numbers tucked under its two ends are its exact feasible range.",
        "Drag sideways along any row to keep only the designs in that band — that is how you pose a question. Click a row again to clear it; hover a row for its exact numbers.",
      ],
      setup: "clear",
    },
    {
      target: '[data-tour="flex"]',
      title: `Ruling out ${short(demoAxis)}`,
      body: [
        `We just brushed it for you: ${short(demoAxis)} held near zero. That teal band on its row is the constraint — drag one yourself on any row, and click a row to clear it.`,
        "Everything else on screen has already updated to this question.",
      ],
      setup: "cap-demo",
    },
    {
      target: '[data-tour="consequences"]',
      title: "Does anything survive?",
      body: [
        "The verdict on your constraints. Whether any feasible design survives at all is decided by a linear program on the polytope — not by whether sampled designs happened to land there — so it is exact however hard you squeeze.",
        "Squeeze hard enough and the sampled cloud gets too thin to describe your region honestly. When that happens this strip offers to resample — a fresh run inside your constraints — rather than draw a distribution out of a handful of dots.",
      ],
      setup: "cap-demo",
    },
    {
      target: '[data-tour="flex"]',
      title: "What it forces",
      body: [
        "Each axis now carries its exact remaining range: an amber band inside a grey track showing what you started with, the surviving span in numbers under the row, and the share of the range still left beside the axis name. This is the interesting part: giving something up does not automatically force anything else.",
        `Look at the amber bands on the other axes. If they still fill their grey tracks, ruling out ${short(demoAxis)} costs you options but forces no other decision — it is a free choice.`,
      ],
      next: "Now try the contrast",
      setup: "cap-demo",
    },
    {
      target: '[data-tour="flex"]',
      title: `…and now without ${short(demoAxis2)}`,
      body: [
        `Same question, different lever: we swapped the constraint to ${short(demoAxis2)}.`,
        "Compare the axes with the previous step. Where an amber band has lifted off the bottom of its grey track, that lever now has a floor — the system has to make up for what you removed. That difference between the two cases is the point of the whole tool.",
      ],
      setup: "cap-contrast",
    },
    {
      title: "Your turn",
      body: [
        "Drag along any row to constrain it; click it again to clear.",
        "Then read the axes for what is forced, and the strip underneath for whether anything survives at all. We have cleared the demo constraint — reopen this guide any time with ? in the top bar.",
      ],
      next: "Explore",
      setup: "clear",
    },
  ]);

  // Each step sets up the app for what it explains, keyed off an explicit `setup`
  // tag. (Deriving it from the target selector or the title looks tidier but two
  // steps share the flex panel, and matching on prose breaks the moment the text
  // or an axis name changes.) Runs on entry, and re-runs when stepping back.
  function applyTourStep(i: number) {
    switch (tourSteps[i]?.setup) {
      case "reset":
        viewMode = "coupling";
        couplingPair = null; couplingFull = false;
        clearScenario();
        break;
      case "facet":
        viewMode = "coupling";
        if (demoAxis && demoAxis2) {
          facetSel = { x: demoAxis, y: demoAxis2 };
          couplingPair = { x: demoAxis, y: demoAxis2 };
        }
        break;
      case "cap-demo":
        couplingPair = null; couplingFull = false;
        capLever(demoAxis);
        break;
      case "cap-contrast":
        capLever(demoAxis2);
        break;
      case "clear":
        couplingPair = null; couplingFull = false;
        clearScenario();
        break;
    }
  }

  function startTour() {
    tourStep = 0;
    tourOpen = true;
    applyTourStep(0);
    try { localStorage.setItem(TOUR_SEEN, "1"); } catch { /* storage disabled */ }
  }
  function gotoTourStep(i: number) {
    tourStep = i;
    applyTourStep(i);
  }
  function endTour() {
    tourOpen = false;
    clearScenario();
    couplingPair = null; couplingFull = false;
  }
  // First visit only: offer the walkthrough once the tool has real data in it.
  // `autoOffered` is a plain let, not state: it must gate this effect without
  // re-triggering it, and it is what stops the tour reopening on every close when
  // localStorage is unavailable (there `seen` would stay empty forever).
  let autoOffered = false;
  $effect(() => {
    if (!started || !meta || !flexBase.length || tourOpen || autoOffered) return;
    let seen = "1";
    try { seen = localStorage.getItem(TOUR_SEEN) ?? ""; } catch { /* storage disabled */ }
    autoOffered = true;
    if (!seen) startTour();
  });

  // ---- settings ----
  /** 20000 -> "20k". The chip is a glance, not a readout — the exact count is in
   *  its tooltip and in Settings. */
  const compactN = (n: number) =>
    n >= 1000 ? `${(n / 1000).toFixed(n % 1000 === 0 || n >= 10000 ? 0 : 1)}k` : `${n}`;

  // What the floating panels cover of the full-bleed map canvas. Mirrors the
  // layout custom properties on `.stage` — keep the two in step, or the cloud
  // will centre on the wrong window.
  const RAIL_W = 348, EDGE = 16, BAR_H = 60;
  const mapInset = { left: RAIL_W + EDGE * 2, top: BAR_H + EDGE, right: EDGE, bottom: EDGE };

  let showSettings = $state(false);

  // ---- theme ----
  // Dark default. The OS preference only supplies the initial value; once the
  // user picks, that choice wins in both directions and persists. `data-theme` on
  // <html> is what the token sheet and the canvas readers key off.
  const THEME_KEY = "ee.theme";
  let theme = $state<"dark" | "light">("dark");
  // Canvas components take this as a prop purely as a redraw trigger (canvases
  // hold baked pixels, so CSS cannot restyle them). It is DERIVED from the theme,
  // not incremented: `themeTick++` inside the effect below read and wrote the same
  // state, which is the self-referencing-effect infinite loop CLAUDE.md warns
  // about — it froze the app, and neither `check` nor `build` can catch it.
  const themeTick = $derived(theme === "light" ? 1 : 0);
  onMount(() => {
    let t: string | null = null;
    try { t = localStorage.getItem(THEME_KEY); } catch { /* storage disabled */ }
    if (t !== "light" && t !== "dark")
      t = window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
    theme = t as "dark" | "light";
  });
  // $effect.pre, NOT $effect. The canvases read their colours off the live CSS
  // variables (colors.ts:canvasTokens), and their render effects also depend on
  // `theme`. Svelte does not order sibling effects, so with a plain $effect the
  // canvas could repaint BEFORE this one wrote `data-theme` — baking the previous
  // theme's values in, with nothing left to re-trigger it. The symptom was a rail
  // stuck exactly one theme behind until you happened to brush it. Pre-effects
  // run before every render effect in the flush, so the attribute (and hence
  // getComputedStyle) is always current by the time a canvas reads it.
  $effect.pre(() => {
    document.documentElement.setAttribute("data-theme", theme);
    setColorTheme(theme);   // ramps + categorical palette follow the theme
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage disabled */ }
  });
  // Map overlay/projection popover. Mutually exclusive with Settings — both
  // anchor to the same top-left corner now that the graph is full-width.
  let showLayers = $state(false);
  // per-dimension on/off (keyed by axis name; missing/true = enabled). Disabled
  // dimensions are hidden from the parallel coords, the Coupling matrix, the facet, and the color
  // pickers. The Map (PCA) projection always uses all technologies.
  let dimEnabled = $state<Record<string, boolean>>({});
  let reduceMotion = $state(false);
  let clusterK = $state(6);
  const activeAxes = $derived((meta?.axes ?? []).filter((a) => dimEnabled[a] !== false));
  const nActive = $derived(activeAxes.length);
  function toggleDim(a: string) {
    // keep at least 2 dimensions on (pairwise views need a pair)
    if (dimEnabled[a] !== false && nActive <= 2) return;
    dimEnabled = { ...dimEnabled, [a]: dimEnabled[a] === false };
  }
  function setAllDims(on: boolean) {
    dimEnabled = Object.fromEntries((meta?.axes ?? []).map((a) => [a, on]));
  }
  // If the colored axis gets disabled, fall back gracefully.
  // Converges in one step: each guard's condition is false once it has fired.
  $effect(() => {
    if (field && dimEnabled[field] === false) { field = ""; loadColor(); }
    // The docked coupling facet must not outlive its axes: without this it keeps
    // captioning a dimension the user just switched off, and — since the split is
    // what hides the validation scatter — leaves it hidden with no cell selected.
    const dropped = (p: { x: string; y: string } | null) =>
      !!p && (dimEnabled[p.x] === false || dimEnabled[p.y] === false);
    if (dropped(couplingPair)) { couplingPair = null; couplingFull = false; }
    if (dropped(facetSel)) facetSel = null;
  });
  // Coupling view honours the toggles: subset the dependence matrices to the active
  // axes only (the DependenceMatrix re-clusters whatever it's given). FacetView does
  // its own filtering from `activeAxes`, so the pair list is passed through whole.
  const depView = $derived.by(() => {
    const dp = depData;
    if (!dp) return null;
    if (activeAxes.length === dp.axes.length) return dp; // all on → pass through
    const idx = dp.axes.map((a, i) => (dimEnabled[a] !== false ? i : -1)).filter((i) => i >= 0);
    const sub = (M: number[][]) => idx.map((i) => idx.map((j) => M[i][j]));
    return {
      ...dp,
      axes: idx.map((i) => dp.axes[i]),
      dcor: sub(dp.dcor), mi: sub(dp.mi), pearson: sub(dp.pearson),
    };
  });

  // ---- axis captions ----
  function topTechs(comp: number[], names: string[], k = 2): string {
    return comp
      .map((w, i) => ({ w, n: names[i] }))
      .sort((a, b) => Math.abs(b.w) - Math.abs(a.w))
      .slice(0, k)
      .map(({ w, n }) => `${w >= 0 ? "↑" : "↓"}${short(n)}`)
      .join(" ");
  }
  function axisLabel(axis: 0 | 1): string {
    if (isStar) return axis === 0 ? "star x · your linear combo (drag anchors)" : "star y";
    if (!proj) return "";
    if (proj.method === "pca" && proj.components && proj.explained_variance) {
      const pct = (proj.explained_variance[axis] * 100).toFixed(1);
      return `PC${axis + 1} · ${pct}% var · ${topTechs(proj.components[axis], proj.feature_names)}`;
    }
    return `${proj.method.toUpperCase()} ${axis + 1}`;
  }
  const isMetric = $derived(method === "pca" || isStar);

  // ---- what the views currently display (candidates / star / full space) ----
  const inCandidates = $derived(!!candidates);
  // Row ids of the rendered subset (full-space view only; candidates carry their
  // own arrays). The full-set arrays (samples / color / normTech) are gathered
  // through this, so dispPoints[i] / dispValues[i] / dispColorValues[i] /
  // dispNormTech[i] all refer to the same design i — selection stays positional.
  const subIndex = $derived(!candidates && proj ? proj.index : null);
  // (same DISPLAY_N-subset-or-gather rule as dispValues below)
  const dispNormTech = $derived.by(() => {
    const nt = normTech;
    if (!subIndex || !nt) return nt;
    return nt.length === subIndex.length ? nt : subIndex.map((r) => nt[r]);
  });
  const dispPoints = $derived(
    candidates ? candidates.points : isStar ? starPoints : (proj?.points ?? []),
  );
  const dispOptimum = $derived(
    !candidates && isStar ? starOptimum : (proj?.optimum ?? null),
  );
  const dispFields = $derived(candidates ? candidates.fields : (samples?.fields ?? []));
  const dispValues = $derived.by(() => {
    if (candidates) return candidates.values;
    const v = samples?.values ?? [];
    if (!subIndex || !v.length) return v;
    // /api/samples is fetched with the same DISPLAY_N as the projection, so it
    // already returns exactly the displayed rows, in order. Only gather by global
    // row id when it handed back the full cloud (no/ignored `sample=`).
    return v.length === subIndex.length ? v : subIndex.map((r) => v[r]);
  });

  const colorIdx = $derived(
    dispFields.indexOf(field === "chain" ? "net_present_cost" : field),
  );
  const dispColorValues = $derived.by(() => {
    if (!candidates) {
      const v = colorData?.values ?? [];
      return subIndex && v.length ? subIndex.map((r) => v[r]) : v;
    }
    if (colorIdx < 0) return [];
    return candidates.values.map((r) => r[colorIdx]);
  });
  const dispCategorical = $derived(!candidates && (colorData?.categorical ?? false));
  const dispColorMin = $derived(
    candidates ? (dispColorValues.length ? Math.min(...dispColorValues) : 0) : (colorData?.min ?? 0),
  );
  const dispColorMax = $derived(
    candidates ? (dispColorValues.length ? Math.max(...dispColorValues) : 1) : (colorData?.max ?? 1),
  );

  // ---- derived data for the analytics overlays / tray ----
  // (gated on the user-chosen method, not proj.method — star fetches PCA internally)
  const compassArrows = $derived(
    method === "pca" && proj?.components
      ? proj.feature_names.map((n, j) => ({
          dx: proj!.components![0][j],
          dy: proj!.components![1][j],
          label: short(n),
        }))
      : null,
  );
  const spokeMarkers = $derived(
    method === "pca" && !inCandidates
      ? extremesData.map((e) => ({
          key: `${e.kind}-${e.axis}`,
          x: e.point[0], y: e.point[1],
          label: `${e.kind} ${short(e.axis)}`,
        }))
      : [],
  );
  const techLabels = $derived(
    (samples?.fields ?? []).filter((f) => f !== "net_present_cost").map(short),
  );
  // per-technology ranges over the served cloud — a stable reference for radar
  // glyphs / kNN that doesn't move with brushes or the candidate set
  const techRanges = $derived.by(() => {
    if (!samples) return [];
    const idx = samples.fields.map((_, j) => j).filter((j) => samples!.fields[j] !== "net_present_cost");
    return idx.map((j) => {
      let lo = Infinity, hi = -Infinity;
      for (const row of samples!.values) {
        if (row[j] < lo) lo = row[j];
        if (row[j] > hi) hi = row[j];
      }
      return { min: lo, max: hi };
    });
  });
  const techValues = (p: Pin) => p.values; // all axes are technologies now

  // Interpolated A→B design (9 technologies) for the morph radar glyph.
  // Mirrors the white parallel-coords overlay.
  const morphValues = $derived(
    pins.length >= 2 ? lerp(pins[0].values, pins[1].values, morphT) : null,
  );
  const morphTechValues = $derived(morphValues ?? []);

  // morph state shown on map & parallel coords (uses live-projected pin points)
  const morphPath = $derived(
    pins.length >= 2 ? { a: pinPoints[0], b: pinPoints[1], t: morphT } : null,
  );
  const pcOverlay = $derived.by(() => {
    if (hoverPin !== null && pins[hoverPin]) return pins[hoverPin].values;
    if (pins.length >= 2 && morphT > 0) return lerp(pins[0].values, pins[1].values, morphT);
    if (pins.length >= 1 && pins.length < 2) return pins[0].values;
    return null;
  });
  // null => ParallelCoords falls back to --tick, i.e. a mark that reads against
  // the CURRENT surface. This used to be a literal "#ffffff", which made the
  // A->B sweep line invisible on a light panel.
  const pcOverlayColor = $derived(
    hoverPin !== null && pins[hoverPin] ? pinColors[hoverPin] : null,
  );
  // The two interpolation endpoints, drawn statically in their pin colors on the
  // parallel coordinates while the white overlay sweeps between them.
  const pcStatic = $derived(
    pins.length >= 2
      ? [
          { values: pins[0].values, color: pinColors[0] },
          { values: pins[1].values, color: pinColors[1] },
        ]
      : [],
  );
  // Pinned designs (e.g. extremes) can lie outside the sampled range; pass their
  // values so the parallel-coords axes widen to keep their lines inside the plot.
  const pcDomainExtra = $derived(pins.map((p) => p.values));

  // ---- coupling-ordered axes (§3) ----
  // Order the parallel-coords axes so strongly-coupled ones (high distance
  // correlation) sit next to each other, making trade-offs read between neighbors.
  let couplingAxes = $state(true);
  // optimum tick on each Profiles row. Distinct from `showOptimum`, which is the
  // Map's ◯ overlay — the rows are in both views, so they need their own flag.
  let showOptTicks = $state(false);
  // Same hierarchical clustering the Coupling matrix uses (see cluster.ts), so the
  // two views agree on what "next to each other" means.
  // Permutation of dispFields column indices; [] means canonical order.
  const axisOrder = $derived.by(() => {
    if (!couplingAxes || !depData?.dcor?.length || !dispFields.length) return [];
    const idx = dispFields.map((f) => depData!.axes.indexOf(f));
    if (idx.some((i) => i < 0)) return [];
    const n = dispFields.length;
    const D = Array.from({ length: n }, (_, i) =>
      Array.from({ length: n }, (_, j) => depData!.dcor[idx[i]][idx[j]] ?? 0));
    return clusterOrder(D);
  });
  function reorder<T>(arr: T[], ord: number[]): T[] { return ord.map((i) => arr[i]); }
  // Column order+filter for ONLY the parallel-coords props: the coupling seriation
  // (or canonical identity) with settings-disabled dimensions dropped. dispFields/
  // dispValues and every other consumer stay canonical & full-width (they index
  // columns by axis name and rows by design id). flexRanges match by axis name.
  const pcCols = $derived.by(() => {
    const base = axisOrder.length ? axisOrder : dispFields.map((_, i) => i);
    return base.filter((i) => dimEnabled[dispFields[i]] !== false);
  });
  const pcFields = $derived(reorder(dispFields, pcCols));
  const pcValues = $derived.by(() => dispValues.map((row) => reorder(row, pcCols)));
  const pcOverlayOrd = $derived(pcOverlay ? reorder(pcOverlay, pcCols) : pcOverlay);
  const pcStaticOrd = $derived(pcStatic.map((s) => ({ values: reorder(s.values, pcCols), color: s.color })));
  const pcDomainExtraOrd = $derived(pcDomainExtra.map((row) => reorder(row, pcCols)));
  // Optimum keyed by axis NAME so it survives the pcCols permutation and the
  // candidate-set field list — no reorder() needed, which is the point.
  // physical unit per axis name, from the polytope file (see /api/meta)
  const unitsByAxis = $derived(
    Object.fromEntries((meta?.axes ?? []).map((a, i) => [a, meta?.units?.[i] ?? ""])));
  // ---- facet shape taxonomy controls (Settings) ----
  let showShapes = $state(true);
  let gapEps = $state(GAP_EPS_DEFAULT);

  // Shape verdict for the docked facet — the plain-language payoff for the
  // outline you are looking at ("ccs_lump at scale requires biomass").
  let pairGaps = $state<CornerGaps | null>(null);
  let shapeSeq = 0;
  $effect(() => {
    const p = couplingPair;
    const my = ++shapeSeq;
    if (!p) { pairGaps = null; return; }
    getShadow(p.x, p.y, []).then((s) => {
      if (my === shapeSeq) pairGaps = s.corner_gaps ?? null;
    }).catch(() => { if (my === shapeSeq) pairGaps = null; });
  });
  // classified here, with the same threshold the matrix uses
  const pairShape = $derived(
    couplingPair && pairGaps
      ? classifyFacet(pairGaps, short(couplingPair.x), short(couplingPair.y), gapEps)
      : null);


  const optimumByAxis = $derived(
    (meta?.axes ?? []).map((a, i) => ({ axis: a, value: meta!.optimum.u_star[i] })));

  // ---- hover tooltip ----
  // hoverIdx is the position within the rendered arrays; dispValues / dispPoints
  // are aligned to it, so the tooltip indexes by hoverRow directly. The original
  // design id (for the label) is recovered via subIndex.
  const hoverRow = $derived(hoverIdx);
  function onMove(e: MouseEvent) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    mouse = [e.clientX - r.left, e.clientY - r.top];
  }
  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(3);
  // percentage formatter that keeps small values legible (0.02%, not 0.0%)
  const pct = (v: number) =>
    v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v >= 0.01 ? v.toFixed(2) : v.toFixed(3);
  // ---- constraint → consequence readout (§4) ----
  // Synthesizes the exact-LP flexibility (flexBase vs flexCur) into decision terms:
  // which technology levers the current constraints force up or cap / which options
  // they foreclose.
  type Consequence = { axis: string; kind: "force" | "include" | "limit"; text: string; mag: number };
  const consequences = $derived.by(() => {
    if (!allConstraints.length || !meta || !flexBase.length) return null;
    if (!flexFeasible) return { infeasible: true, items: [] as Consequence[] };
    const baseBy = new Map(flexBase.map((r) => [r.axis, r]));
    const items: Consequence[] = [];
    for (const cur of flexCur) {
      if (cur.min == null || cur.max == null) continue;
      const base = baseBy.get(cur.axis);
      if (!base || base.min == null || base.max == null) continue;
      const span = base.max - base.min || 1;
      const rose = cur.min - base.min;   // feasible floor lifted → forced up
      const fell = base.max - cur.max;   // feasible ceiling dropped → capped
      if (rose > 0.03 * span) {
        const excludable = base.min <= 0.001 * span; // could be ~0 before
        items.push(excludable
          ? { axis: cur.axis, kind: "include", mag: rose / span, text: `must build ${short(cur.axis)} ≥ ${fmt(cur.min)}` }
          : { axis: cur.axis, kind: "force", mag: rose / span, text: `${short(cur.axis)} forced ≥ ${fmt(cur.min)}` });
      }
      if (fell > 0.03 * span) {
        items.push({ axis: cur.axis, kind: "limit", mag: fell / span, text: `${short(cur.axis)} capped ≤ ${fmt(cur.max)}` });
      }
    }
    items.sort((a, b) => b.mag - a.mag);
    return { infeasible: false, items: items.slice(0, 6) };
  });

  // ---- profile marginals measured in the SHOWN space (worldview B) ----
  // Reading the full-space cloud along an axis weights every combination of the
  // shown axes by how many ways the HIDDEN axes can realize it, so hiding an
  // axis left every violin pixel-identical — the geometric no-op. Worldview B
  // instead treats the shown axes as the design space: uniform over pi_S(P), the
  // reachable combinations, each counted once. Under B, disabling an axis
  // genuinely reshapes the remaining distributions.
  //
  // Only fetched for a STRICT subset: with everything shown pi_S(P) = P and the
  // base cloud already is the answer (and is far larger than we could sample).
  // In candidate mode the plot is showing designs generated inside the region,
  // which is its own distribution and must not be overwritten.
  // Debounced + sequence-guarded: axis toggles arrive in bursts and the sweep
  // costs ~3.5 ms/sample, so out-of-order replies would clobber newer state.
  const subsetActive = $derived(nActive > 0 && nActive < (meta?.axes.length ?? 0));
  let marginals = $state<Marginals | null>(null);
  let marginalBusy = $state(false);
  // The violins fall back to the full-space cloud when this fetch fails. That is
  // a DIFFERENT distribution, not a degraded one, so it has to be said out loud
  // rather than silently drawn as if it were the projection.
  let marginalFailed = $state(false);
  let marginalSeq = 0;
  let marginalTimer: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const axes = activeAxes, sub = subsetActive, cand = inCandidates;
    const my = ++marginalSeq;
    if (!sub || cand || !axes.length) {
      marginals = null; marginalBusy = false; marginalFailed = false;
      return;
    }
    // Drop the previous axis set's sample immediately. `pcMarginals` maps by axis
    // NAME, so after hiding one more axis the old sample still has every column
    // it asks for and would render happily — a projection onto the WRONG set of
    // axes, shown as if it were the answer, for as long as the refetch takes.
    // Falling back to the full-space marginal meanwhile is at least a real
    // distribution, and the header says it is being recomputed.
    marginals = null;
    marginalBusy = true;
    marginalTimer = setTimeout(async () => {
      try {
        const r = await getMarginals(axes);
        if (my === marginalSeq) { marginals = r; marginalFailed = false; }
      } catch {
        if (my === marginalSeq) { marginals = null; marginalFailed = true; }
      } finally { if (my === marginalSeq) marginalBusy = false; }
    }, 350);
    return () => { if (marginalTimer) clearTimeout(marginalTimer); };
  });

  // Permute the projected sample's columns onto `pcFields`. Keyed by axis NAME:
  // pcFields carries the coupling seriation, so an index-keyed map would put a
  // distribution on the wrong row.
  const pcMarginals = $derived.by(() => {
    const m = marginals;
    if (!m || !m.values.length) return null;
    const cols = pcFields.map((f) => m.axes.indexOf(f));
    if (cols.some((c) => c < 0)) return null;   // stale reply for another axis set
    return m.values.map((row) => cols.map((c) => row[c]));
  });

  // ---- how many sampled designs land inside the constraints ----
  // This is a statement about the CLOUD, not about the space: it decides whether
  // the sample is dense enough here to be worth drawing, and nothing else.
  //
  // A "% of the space left" headline used to live here — a Monte-Carlo volume
  // ratio, plus a projected variant (worldview B) for when axes were hidden.
  // Both were removed: the two disagreed in both directions, the projected one
  // was not monotone under hiding an axis, and a single percentage implied a
  // precision the estimate did not have. What the UI still says about a
  // constraint is exact: the LP feasibility verdict and the per-axis ranges.
  //
  // ALWAYS counted against the uniform base cloud, never `dispValues`. In
  // candidate mode dispValues IS the constrained region (hit-and-run run inside
  // it), so every row satisfies the constraints and the count is meaningless.
  const VOL_TRUST_K = 200; // below this many in-region samples, offer a resample
  const inRegion = $derived.by(() => {
    const cons = allConstraints;
    const rows = samples?.values ?? [];
    const flds = samples?.fields ?? [];
    const n = rows.length;
    if (!cons.length || !n) return null;
    const cols = cons
      .map((c) => ({ j: flds.indexOf(c.axis), min: c.min, max: c.max }))
      .filter((c) => c.j >= 0);
    if (!cols.length) return null;
    let k = 0;
    for (const row of rows) {
      let ok = true;
      for (const c of cols) {
        const v = row[c.j];
        if (c.min != null && v < c.min) { ok = false; break; }
        if (c.max != null && v > c.max) { ok = false; break; }
      }
      if (ok) k++;
    }
    return { k, n };
  });

</script>

{#if restoring}
  <div class="restoring">restoring your dataset…</div>
{:else if !started}
  <Landing onready={onLandingReady}
           oncancel={resumeMeta ? resumeSession : null}
           currentName={resumeMeta?.dataset.name ?? null} />
{:else}
<div class="stage" role="presentation" onmousemove={onMove}>
  <!-- top bar -->
  <header class="hud panel topbar">
    <!-- Left = who you are and what is loaded. This used to be the wordmark plus
         THREE identically-styled word pills, which gave the rarest and most
         disruptive action (rebuilding the session on a new dataset) the most
         prominent slot and left the loaded dataset's name visible only inside
         Settings. Now the name IS the control. -->
    <div class="brand">
      <h1>Energy Explorer</h1>
      <button class="ds-chip" onclick={changeDataset}
              title="{meta?.dataset.name ?? 'dataset'} · {meta?.n_samples.toLocaleString() ?? '—'} designs · {meta?.axes.length ?? '—'} technologies — click to pick or upload another">
        <span class="ds-name">{meta?.dataset.name ?? "—"}</span>
        {#if meta}<span class="ds-n">{compactN(meta.n_samples)}</span>{/if}
      </button>
    </div>
    <div class="seg view-seg" data-tour="views">
      <button class:active={viewMode === "coupling"} onclick={() => (viewMode = "coupling")}>Coupling</button>
      <button class:active={viewMode === "map"} onclick={() => (viewMode = "map")}>Map</button>
    </div>
    <!-- Right = the view's own context, then the two global actions. The Map's
         gestures are invisible affordances on a WebGL canvas, so a short reminder
         earns its place; the Coupling sentence was deleted — the matrix already
         carries its own title, metric toggle, colour legend and per-pair caption,
         so it was a paragraph restating the panel below it. -->
    <div class="bar-right">
      {#if proj && viewMode === "map"}
        <span class="topbar-hint">
          {mode === "select" ? "drag to lasso-select" : "scroll zoom · drag pan · click to pin"}{#if isStar} · drag ◯ to rotate{/if}{#if !isMetric} · {method.toUpperCase()} is non-metric{/if}
        </span>
      {/if}
      <button class="icon-btn" class:on={showSettings} title="settings"
              aria-label="settings"
              onclick={() => { showSettings = !showSettings; if (showSettings) showLayers = false; }}>⚙</button>
      <button class="icon-btn" class:on={tourOpen} title="guided walkthrough"
              aria-label="guided walkthrough" onclick={startTour}>?</button>
    </div>
  </header>

  {#if showSettings}
    <!-- Ordered by how often it is touched, and SCOPED to the current view: the
         facet threshold means nothing on the Map and the cluster count means
         nothing in Coupling, so showing both everywhere made a six-section wall
         of which two sections were always inert. Appearance absorbed the orphan
         "Reduce motion" checkbox, which used to sit at the bottom under no
         heading at all. -->
    <div class="settings-pop hud panel">
      <div class="settings-head">
        <span>Settings</span>
        <button class="cpl-close" aria-label="close settings" onclick={() => (showSettings = false)}>✕</button>
      </div>

      <div class="set-sect">
        <div class="set-label"><span>Color by</span></div>
        <select bind:value={field} onchange={loadColor}>
          <option value="">— none —</option>
          {#each activeAxes as a}<option value={a}>{short(a)}</option>{/each}
        </select>
        <span class="muted small">encodes one technology as colour across every view.</span>
      </div>

      <div class="set-sect">
        <div class="set-label">
          <span>Visible axes</span>
          <span class="set-actions">
            <button class="link" onclick={() => setAllDims(true)} disabled={nActive === (meta?.axes.length ?? 0)}>show all</button>
          </span>
        </div>
        {#each meta?.axes ?? [] as a}
          <label class="check dim">
            <input type="checkbox" checked={dimEnabled[a] !== false}
                   disabled={dimEnabled[a] !== false && nActive <= 2}
                   onchange={() => toggleDim(a)} />
            {short(a)}
          </label>
        {/each}
        <!-- This paragraph used to say hiding was "display only" and that the
             violins were "already marginals over the full space". Both stopped
             being true when the violins moved into the projection — see
             PROCESSES.md §4b. Getting this wrong is exactly the hide-vs-constrain
             confusion the docs warn about, so it is worth the words. -->
        <span class="muted small">
          {nActive} of {meta?.axes.length ?? 0} shown. Hiding an axis does
          <strong>not</strong> narrow the space — it still constrains every other one, and
          nothing here can make an infeasible set feasible. What it does change is the
          question the <strong>Profiles</strong> distributions answer: they are re-measured
          over the combinations of the axes you kept, so the violins reshape.
          Coupling values, facets and the Map are unaffected. To actually narrow the
          space, drag along a row.
        </span>
      </div>

      {#if viewMode === "coupling"}
        <div class="set-sect">
          <div class="set-label"><span>Facet shapes</span></div>
          <label class="check"><input type="checkbox" bind:checked={showShapes} /> colour the matrix by category</label>
          {#if showShapes}
            <label class="range-row" title="how much of a corner must be cut before it is reported — a materiality choice, not a measurement limit">
              <input type="range" min="0.05" max="0.35" step="0.01" bind:value={gapEps} />
              <span class="range-val">{gapEps.toFixed(2)}</span>
            </label>
            <span class="muted small">
              threshold on the corner gap. The gaps are exact to ~1e-3, so this only sets
              what counts as worth reporting — slide it to see which calls are borderline.
            </span>
          {/if}
        </div>
      {:else}
        <div class="set-sect">
          <div class="set-label"><span>Map clusters</span></div>
          <label class="range-row">
            <input type="range" min="2" max="12" step="1" bind:value={clusterK} onchange={loadClusters} />
            <span class="range-val">{clusterK}</span>
          </label>
          <span class="muted small">number of k-means groups labelled on the Map.</span>
        </div>
      {/if}

      <div class="set-sect">
        <div class="set-label"><span>Appearance</span></div>
        <div class="seg">
          <button class:active={theme === "dark"} onclick={() => (theme = "dark")}>Dark</button>
          <button class:active={theme === "light"} onclick={() => (theme = "light")}>Light</button>
        </div>
        <label class="check"><input type="checkbox" bind:checked={reduceMotion} /> Reduce motion</label>
        <span class="muted small">disable the facet slide-in and other animations.</span>
      </div>

      <div class="set-sect">
        <div class="set-label"><span>Dataset</span></div>
        <div class="data-name" title={meta?.dataset.name}>{meta?.dataset.name ?? "—"}</div>
        {#if meta}
          <span class="muted small">
            <strong>{Math.min(DISPLAY_N, meta.n_samples).toLocaleString()}</strong> of
            {meta.n_samples.toLocaleString()} designs shown · {meta.axes.length} technologies{#if proj} ·
            {proj.method.toUpperCase()}{/if}
          </span>
        {/if}
        <span class="muted small">click the dataset name in the top bar to pick or upload another polytope.</span>
      </div>
    </div>
  {/if}

  <!-- borderless graph between the panels -->
  {#if viewMode === "coupling"}
    <div class="graph-region coupling-split" class:split={couplingPair} class:facet-full={couplingPair && couplingFull}>
      <div class="cpl-matrix" data-tour="matrix">
        <DependenceMatrix
          dep={depView}
          pairs={shadowPairs}
          metric={depMetric}
          {showShapes}
          {gapEps}
          onmetric={(m) => (depMetric = m)}
          onpair={(x, y) => { facetSel = { x, y }; couplingPair = { x, y }; }}
        />
      </div>
      {#if couplingPair}
        <div class="cpl-facet" transition:fly={{ x: 60, duration: reduceMotion ? 0 : 280, easing: cubicOut }}>
          <div class="cpl-facet-head">
            <span class="cpl-facet-title">
              exact facet · {short(couplingPair.x)} × {short(couplingPair.y)}
              {#if pairShape}
                <span class="shape-tag" style="--c:{CAT[pairShape.category].color}">
                  {CAT[pairShape.category].label}{#if pairShape.borderline} ?{/if}
                </span>
              {/if}
            </span>
            <button class="cpl-full" data-tour="full-view" title={couplingFull ? "back to the matrix" : "expand the facet over the matrix"}
                    onclick={() => (couplingFull = !couplingFull)}>
              {couplingFull ? "⛶ exit full view" : "⛶ full view"}
            </button>
            <button class="cpl-close" title="close facet panel" aria-label="close facet panel"
                    onclick={() => { couplingPair = null; couplingFull = false; }}>✕</button>
          </div>
          {#if pairShape?.detail}
            <p class="shape-detail">
              {pairShape.detail}{#if pairShape.category !== "independent"}{" · "}{(pairShape.strength * 100).toFixed(0)}% of the corner is ruled out{/if}
            </p>
          {/if}
          {#if dispFields.length}
            <div class="cpl-facet-body">
              <FacetView
                fields={dispFields}
                values={dispValues}
                activeFields={activeAxes}
                colorValues={dispColorValues}
                colorCategorical={dispCategorical}
                theme={theme}
                colorMin={dispColorMin}
                colorMax={dispColorMax}
                constraints={allConstraints}
                selected={candidates ? null : selected}
                selectPair={facetSel}
                {shadowPairs}
                onconsumepair={() => (facetSel = null)}
              />
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {:else if viewMode === "map" && proj}
    <!-- Full-bleed: the canvas runs under the top bar and the Profiles rail so
         there is no strip of page showing between them. `inset` keeps the DATA
         inside the visible window — without it the cloud centres on the whole
         canvas and a fifth of it hides behind the rail. -->
    <div class="graph-region bleed">
      <ScatterGL
        inset={mapInset}
        bind:this={scatter}
        {themeTick}
        points={dispPoints}
        color={dispColorValues}
        categorical={dispCategorical}
        optimum={dispOptimum}
        {showOptimum}
        clusters={inCandidates || isStar ? [] : clusterMarkers}
        showClusters={showClusters && !inCandidates && !isStar}
        compass={compassArrows}
        showCompass={showCompass && !isStar}
        {showDensity}
        spokes={spokeMarkers}
        {showSpokes}
        pins={pins.map((p, i) => ({ id: p.id, letter: String.fromCharCode(65 + i), point: pinPoints[i], color: pinColors[i] }))}
        path={morphPath}
        walkChains={inCandidates ? 0 : 4}
        walkActive={walkOn && !inCandidates}
        {selected}
        {mode}
        onhover={(i) => (hoverIdx = i)}
        onselect={(idx) => { clearBrushConstraints(); selected = idx.length ? idx : null; simPin = null; }}
        onpin={pinRow}
        onspoke={pinExtreme}
        draggableMarkers={isStar && !inCandidates}
        onmarkerdrag={onMarkerDrag}
      />
    </div>
    <!-- outside the bleeding region, so the axis names stay clear of the panels -->
    <div class="map-labels">
      <div class="ylabel">{axisLabel(1)}</div>
      <div class="xlabel">{axisLabel(0)}</div>
    </div>
  {/if}

  <!-- shown in every view: it carries the only way back out of candidate mode -->
  {#if inCandidates && candidates}
    <div class="hud gen-banner panel">
      <span><span class="dot"></span><strong>{candidates.n.toLocaleString()}</strong> generated candidates</span>
      <button class="link" onclick={backToFull}>← back to full space</button>
    </div>
  {/if}

  {#if loading}
    <div class="overlay">Computing projection…</div>
  {:else if error}
    <div class="overlay err">⚠ {error}</div>
  {/if}

  <!-- tooltip floats above every panel -->
  {#if hoverRow !== null && dispValues[hoverRow]}
    <div class="tooltip" style="left:{mouse[0] + 14}px; top:{mouse[1] + 14}px">
      <div class="tt-title">{inCandidates ? "candidate" : "design"} #{!inCandidates && subIndex ? subIndex[hoverRow] : hoverRow}</div>
      {#each dispFields as f, j}
        <div class="tt-row">
          <span>{short(f)}</span>
          <span>{fmt(dispValues[hoverRow][j])} <em>{shortUnit(unitsByAxis[f])}</em></span>
        </div>
      {/each}
    </div>
  {/if}

  <!-- Map-only controls. These used to be the bulk of the left rail; they only
       ever apply to the scatter, so they live on it now. -->
  {#if viewMode === "map"}
    <button class="map-tool" class:on={showLayers} title="projection & overlays"
            onclick={() => { showLayers = !showLayers; if (showLayers) showSettings = false; }}>
      ▤ layers
    </button>
    {#if showLayers}
      <div class="layers-pop hud panel">
        <div class="settings-head">
          <span>Layers</span>
          <button class="cpl-close" aria-label="close layers" onclick={() => (showLayers = false)}>✕</button>
        </div>

        <div class="set-sect">
          <label class="row-label">Projection
            <select bind:value={method} onchange={onMethodChange}>
              {#each meta?.methods ?? ["pca"] as m}<option value={m}>{m.toUpperCase()}</option>{/each}
              <option value="star">STAR ✦ (drag anchors)</option>
            </select>
          </label>
          {#if isStar}
            <div class="star-box">
              {#if normTech && starAnchors.length}
                <StarWheel
                  anchors={starAnchors}
                  labels={techLabels}
                  {touring}
                  onchange={(a) => (starAnchors = a)}
                  ontour={toggleTour}
                  onresetPca={() => (starAnchors = pcaAnchors())}
                  onresetCircle={() => (starAnchors = circleAnchors())}
                />
              {:else}
                <span class="muted small">loading normalized samples…</span>
              {/if}
            </div>
          {/if}
        </div>

        <div class="set-sect">
          <div class="set-label"><span>Overlays</span></div>
          <label class="check"><input type="checkbox" bind:checked={showOptimum} /> cost optimum ◯</label>
          <label class="check"><input type="checkbox" bind:checked={showClusters} /> region labels</label>
          <label class="check"><input type="checkbox" bind:checked={showDensity} /> option topography</label>
          <label class="check" class:dis={method !== "pca"}>
            <input type="checkbox" bind:checked={showCompass} disabled={method !== "pca"} /> tech compass
          </label>
          <label class="check" class:dis={method !== "pca" || inCandidates}>
            <input type="checkbox" bind:checked={showSpokes} disabled={method !== "pca" || inCandidates} /> extreme designs
          </label>
          <label class="check" class:dis={inCandidates}>
            <input type="checkbox" bind:checked={walkOn} disabled={inCandidates} /> sampler walk
          </label>
        </div>

        <div class="set-sect">
          <div class="set-label"><span>Drag mode</span></div>
          <div class="seg">
            <button class:active={mode === "pan"} onclick={() => (mode = "pan")}>Pan</button>
            <button class:active={mode === "select"} onclick={() => (mode = "select")}>Select</button>
          </div>
          <button class="wide" onclick={() => scatter?.reset()}>Reset view</button>
        </div>
      </div>
    {/if}
  {/if}


  <!-- pinned designs (graph top-right, under the ▤ layers button): radar glyphs, A→B morph -->
  {#if viewMode === "map" && (pins.length || proj?.optimum)}
    <section class="hud panel tray">
      <div class="tray-head">
        <span>Pinned designs</span>
        <button class="link" onclick={pinOptimum} title="pin the cost optimum">+ optimum</button>
      </div>

      {#if !pins.length}
        <span class="muted small">click a point (or an extreme ◇) to pin it</span>
      {/if}

      {#each pins as p, i (p.id)}
        <div
          class="card"
          role="group"
          style="--pc:{pinColors[i]}"
          onmouseenter={() => (hoverPin = i)}
          onmouseleave={() => (hoverPin = null)}
        >
          <div class="card-head">
            <span class="badge">{String.fromCharCode(65 + i)}</span>
            <span class="card-label">{p.label}</span>
            <button class="x" onclick={() => removePin(i)}>×</button>
          </div>
          <div class="card-body">
            <RadarGlyph
              values={techValues(p)}
              ranges={techRanges}
              labels={techLabels}
              color={pinColors[i]}
              size={92}
            />
            <div class="card-actions">
              <button class="mini" class:active={simPin === p.id} onclick={() => findSimilar(p)}>
                {simPin === p.id ? "clear similar" : "similar"}
              </button>
            </div>
          </div>
        </div>
      {/each}

      {#if pins.length >= 2}
        <div class="morph">
          <div class="morph-head">
            <span>path <strong>A → B</strong></span>
            <button class="mini" onclick={toggleMorph}>{morphPlaying ? "⏸" : "▶"}</button>
          </div>
          <input type="range" min="0" max="1" step="0.01" bind:value={morphT} />
          {#if morphTechValues.length}
            <div class="morph-glyph">
              <RadarGlyph
                values={morphTechValues}
                ranges={techRanges}
                labels={techLabels}
                color="var(--tick)"
                size={108}
              />
              <span class="morph-cap muted small">
                interpolated · t={morphT.toFixed(2)}
              </span>
            </div>
          {/if}
          <span class="muted small">every point on this path is a feasible near-optimal design (convexity ✓)</span>
        </div>
      {/if}
    </section>
  {/if}

  <!-- Profiles: one horizontal row per technology (the rail of every view) -->
  {#if dispValues.length}
    <section class="hud panel pc-panel" data-tour="pcoords">
      <div class="pc-head">
        <span class="pc-title" title="drag along a row to filter · click it again to clear · hover for its exact range">
          Profiles · {pcFields.length} axes
          {#if inCandidates}<span class="muted">· generated</span>
          {:else if subsetActive && marginalBusy}
            <span class="busy-tag" class:still={reduceMotion}
                  title="the distributions are being re-measured over the {nActive} axes you kept — this runs on the server and takes a few seconds">
              <span class="busy-dot" aria-hidden="true"></span>recomputing distributions
            </span>
          {:else if subsetActive && marginalFailed}<span class="muted"
            title="the distribution over the {nActive} shown axes could not be measured, so these are the full-space marginals — the same shape you would see with every axis on">· full-space marginals</span>
          {/if}
        </span>
        {#if colorData}
          <!-- inline, not a floating chip: in the Coupling view a chip over the
               graph lands on the dependence matrix's own legend -->
          <span class="pc-legend" title="colored by {short(colorData.field)}">
            <span class="ramp" style="background:{markGradient(theme)}"></span>
            <span class="ramp-ends">{fmt(colorData.min)}–{fmt(colorData.max)}</span>
          </span>
        {/if}
        <span class="head-opts">
          <label class="check small" title="order rows so strongly-coupled ones (distance correlation) are adjacent">
            <input type="checkbox" bind:checked={couplingAxes} /> coupled order
          </label>
          <label class="check small" title="mark the cost-optimal value on each row (white tick)">
            <input type="checkbox" bind:checked={showOptTicks} /> optimum
          </label>
        </span>
      </div>

      <!-- #2: the headline sits above the rows it describes, not buried below -->
      <div class="vol-slot" data-tour="consequences">
        <ConsequenceStrip
          variant="headline"
          {consequences}
          {inRegion}
          {flexBusy}
          resampling={generating}
          error={genMsg}
          {inCandidates}
          trustK={VOL_TRUST_K}
          onresample={generateNow}
          selectedCount={selected?.length ?? null}
          onclearselection={clearSelection}
        />
      </div>


      <div class="pc-body" data-tour="flex">
        <ParallelCoords
          bind:this={pcoords}
          fields={pcFields}
          values={pcValues}
          colorValues={dispColorValues}
          colorCategorical={dispCategorical}
          colorMin={dispColorMin}
          colorMax={dispColorMax}
          overlay={pcOverlayOrd}
          overlayColor={pcOverlayColor}
          staticLines={pcStaticOrd}
          domainExtra={pcDomainExtraOrd}
          showViolins={true}
          flexRanges={flexCur.length ? flexCur : flexBase}
          {flexBase}
          optimum={optimumByAxis}
          units={unitsByAxis}
          showOptimum={showOptTicks}
          {flexFeasible}
          {flexBusy}
          marginalValues={pcMarginals}
          {marginalBusy}
          {theme}
          {selected}
          onbrush={(rows, cons) => { selected = rows; brushConstraints = cons; simPin = null; }}
        />
      </div>

      <!-- Directly under the rows it annotates, ABOVE the key. It used to sit at
           the very bottom, below a static legend — which buried the one block
           here that responds to what you just did. -->
      <div class="lim-slot">
        <ConsequenceStrip variant="limits" {consequences} {flexBusy} />
      </div>

      <!-- Key for the marks drawn on each row. Swatches are mini-SVGs of the real
           marks, not colour chips, because the marks differ in shape as well as
           hue (a band, a tick and a density blob read differently).
           ONE LINE, always — including with the optimum tick on, which is the
           widest case. It is `nowrap`, and the swatch/gap sizes below are set so
           all four entries fit the fixed 348 px rail; adding a fifth, or a longer
           label, will silently clip rather than wrap. The violin had a "designs"
           entry here and it was removed: it is the row's primary mark and needs no
           legend, and dropping it is what buys the optimum entry its space. -->
      <div class="pc-key">
        <span class="k" title="exact feasible range under your constraints (LP on the polytope)">
          <svg viewBox="0 0 22 10" aria-hidden="true">
            <rect x="3" y="2" width="16" height="6" fill="var(--amber-soft)" />
            <line x1="3" y1="1.5" x2="3" y2="8.5" stroke="var(--amber-line)" stroke-width="1.2" />
            <line x1="19" y1="1.5" x2="19" y2="8.5" stroke="var(--amber-line)" stroke-width="1.2" />
          </svg>feasible
        </span>
        <span class="k" title="the full near-optimal range before your constraints — only drawn once they differ">
          <svg viewBox="0 0 22 10" aria-hidden="true">
            <rect x="1" y="2" width="20" height="6" fill="var(--neutral-fill)" />
            <line x1="1" y1="1.5" x2="1" y2="8.5" stroke="var(--neutral-line)" />
            <line x1="21" y1="1.5" x2="21" y2="8.5" stroke="var(--neutral-line)" />
          </svg>full range
        </span>
        <span class="k" title="the band you dragged, and the resulting subset's distribution">
          <svg viewBox="0 0 22 10" aria-hidden="true">
            <rect x="6" y="1" width="10" height="8" rx="1"
                  fill="color-mix(in srgb, var(--accent) 18%, transparent)" stroke="var(--accent)" />
          </svg>your cut
        </span>
        {#if showOptTicks}
          <span class="k" title="the cost-optimal value for this technology">
            <svg viewBox="0 0 22 10" aria-hidden="true">
              <line x1="11" y1="0.5" x2="11" y2="9.5" stroke="var(--tick)" stroke-width="2" opacity="0.85" />
            </svg>optimum
          </span>
        {/if}
      </div>

    </section>
  {/if}

  {#if tourOpen}
    <Tour steps={tourSteps} step={tourStep} onstep={gotoTourStep} onclose={endTour} />
  {/if}
</div>
{/if}

<style>
  /* Two bands: a full-width graph over a full-width Profiles panel. These four
     numbers are the whole layout — they used to be copy-pasted into .graph-region
     and .pc-panel separately (and drifted). --graph-top = topbar 16+48+16. */
  /* Profiles rail left, graph right. These four numbers are the whole layout —
     they used to be copy-pasted per rule and drifted. --graph-top = 16+48+16. */
  .stage {
    position: relative; flex: 1; min-height: 0; overflow: hidden;
    --edge: 16px; --bar-h: 60px; --graph-top: calc(var(--bar-h) + var(--edge));
    --rail-w: 348px;
    --graph-left: calc(var(--rail-w) + var(--edge) * 2);
  }

  .hud { position: absolute; z-index: 2; }
  /* Frosted glass, no cast shadow. With the drop shadow gone the only things
     separating a panel from what is behind it are the blur, the wash and the
     border — so the blur is heavy and the border does real work. `saturate` is
     what makes it read as frost rather than as a grey veil: it keeps the colour
     of whatever is underneath alive instead of washing it to neutral. */
  .panel {
    background: var(--panel-glass);
    border: 1px solid var(--b-12);
    border-radius: 16px;
    backdrop-filter: blur(22px) saturate(180%);
    -webkit-backdrop-filter: blur(22px) saturate(180%);
  }

  /* Pinned flush to the top edge and square: it is chrome, not a floating card,
     and a rounded panel inset by 16px left a strip of page showing above it that
     read as a gap once the background went flat. Overrides .panel's radius. */
  .topbar {
    top: 0; left: 0; right: 0; height: var(--bar-h); z-index: 4;
    display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 20px;
    padding: 0 22px;
    border-radius: 0;
    border-width: 0 0 1px;
  }
  .bar-right {
    display: flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 0;
  }
  /* center column keeps the view selector fixed regardless of the side text widths */
  .view-seg { flex: none; justify-self: center; }
  .view-seg button { padding: 6px 20px; font-size: 12.5px; }
  .star-box {
    display: flex; justify-content: center; padding: 4px 0;
    border: 1px dashed color-mix(in srgb, var(--accent) 25%, transparent); border-radius: 12px;
  }
  h1 { font-size: 19px; margin: 0; font-weight: 600; flex: none; letter-spacing: -0.01em; }
  .brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
  /* the only thing here allowed to shrink: an uploaded polytope can have a long
     name, and it must not push the icon buttons off the bar */
  .ds-chip {
    display: flex; align-items: center; gap: 8px; min-width: 0;
    font-size: 11.5px; padding: 5px 11px; border-radius: 8px;
    background: var(--s-05); border: 1px solid var(--b-12);
    color: var(--muted); cursor: pointer; backdrop-filter: blur(6px);
  }
  .ds-chip:hover { background: var(--s-09); color: var(--fg); border-color: var(--accent); }
  .ds-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 26ch; }
  .ds-n {
    flex: none; font-variant-numeric: tabular-nums; font-size: 10px;
    padding: 0 5px; border-radius: 999px;
    background: var(--s-09); color: var(--muted);
  }
  /* subordinate to the chip on purpose — square, icon-only, no border until hover */
  .icon-btn {
    flex: none; width: 32px; height: 32px; padding: 0; border-radius: 9px;
    display: grid; place-items: center; font-size: 16px; line-height: 1;
    background: var(--s-05); border: 1px solid transparent;
    color: var(--muted); cursor: pointer;
  }
  .icon-btn:hover { background: var(--s-09); color: var(--fg); border-color: var(--b-20); }
  .icon-btn.on { border-color: var(--accent); color: var(--accent); background: var(--s-05); }

  /* Anchored under its own button, which now lives at the bar's right end. It
     shares that corner with `.layers-pop` — harmless, because showSettings and
     showLayers are mutually exclusive — and covers the `▤ layers` button while
     open, which is fine for a transient panel the user just asked for. */
  .settings-pop {
    top: calc(var(--bar-h) + 8px); right: var(--edge); width: 264px; z-index: 7;
    display: flex; flex-direction: column; gap: 14px; padding: 14px 16px;
    max-height: calc(100% - var(--bar-h) - 24px); overflow-y: auto;
  }
  .settings-head {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; color: var(--fg);
  }
  .set-sect { display: flex; flex-direction: column; gap: 6px;
    border-top: 1px solid var(--rule); padding-top: 12px; }
  .set-sect:first-of-type { border-top: none; padding-top: 0; }
  .set-label { display: flex; align-items: center; justify-content: space-between;
    font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
  .set-actions { display: flex; gap: 8px; }
  .check.dim { font-variant-numeric: tabular-nums; }
  .range-row { flex-direction: row; align-items: center; gap: 10px; }
  .range-row input { flex: 1; accent-color: var(--accent); }
  .range-val { font-size: 12px; color: var(--fg); min-width: 16px; text-align: right; }
  .topbar-hint {
    color: var(--muted); font-size: 11.5px; text-align: right; min-width: 0;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  .graph-region {
    position: absolute; z-index: 1;
    top: var(--graph-top); left: var(--graph-left); right: var(--edge); bottom: var(--edge);
  }
  /* Map only: edge to edge, under the bar and the rail. The Coupling matrix must
     NOT do this — it is discrete cells, and half a column behind a panel is
     unreadable in a way a point cloud is not. */
  .graph-region.bleed { top: 0; left: 0; right: 0; bottom: 0; }
  .map-labels {
    position: absolute; z-index: 2; pointer-events: none;
    top: var(--graph-top); left: var(--graph-left); right: var(--edge); bottom: var(--edge);
  }
  /* Coupling view: matrix alone by default; when a cell is clicked the facet
     panel docks into the right half and the matrix keeps the left half. */
  .coupling-split { display: flex; gap: 14px; min-height: 0; overflow: hidden; }
  .coupling-split .cpl-matrix { flex: 1 1 100%; min-width: 0; min-height: 0; }
  .coupling-split.split .cpl-matrix { flex: 1 1 50%; }
  /* ⛶ full view: the facet takes the whole region and the matrix steps aside. */
  .coupling-split.facet-full .cpl-matrix { display: none; }
  .coupling-split.facet-full .cpl-facet { border-left: none; padding-left: 0; }
  /* facet panel: a header row (title + full view + close) above the FacetView, so
     the buttons never overlap FacetView's own controls (e.g. ⇄ swap axes). */
  .cpl-facet {
    flex: 1 1 50%; min-width: 0; min-height: 0;
    display: flex; flex-direction: column; gap: 6px;
    border-left: 1px solid var(--b-08); padding-left: 14px;
  }
  .cpl-facet-head { flex: none; display: flex; align-items: center; gap: 8px; }
  .cpl-facet-title {
    flex: 1; min-width: 0;
    font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .cpl-full {
    flex: none; font-size: 11px; padding: 3px 9px; border-radius: 7px; line-height: 1.3;
    background: var(--s-05); border: 1px solid var(--b-12);
    color: var(--muted); cursor: pointer; white-space: nowrap;
  }
  .cpl-full:hover { background: var(--s-09); border-color: var(--accent); color: var(--fg); }
  .cpl-facet-body { flex: 1; min-height: 0; position: relative; }
  /* facet shape verdict — the taxonomy's plain-language payoff, next to the
     outline it describes (see generate.py `classify_facet`) */
  .shape-tag {
    display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
    font-size: 9.5px; letter-spacing: 0.04em; text-transform: none;
    color: var(--c); background: color-mix(in srgb, var(--c) 18%, transparent);
    border: 1px solid color-mix(in srgb, var(--c) 55%, transparent);
  }
  .shape-detail { flex: none; margin: 0; font-size: 11.5px; color: var(--fg); }
  .cpl-close {
    flex: none; display: flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 6px; padding: 0; font-size: 12px; line-height: 1;
    background: var(--s-05); border: 1px solid var(--b-12);
    color: var(--muted); cursor: pointer;
  }
  .cpl-close:hover { background: var(--s-09); color: var(--fg); }
  .ylabel {
    text-shadow: 0 1px 6px var(--shadow-text);
    position: absolute; left: 0; top: 50%;
    transform: translateY(-50%) rotate(180deg); writing-mode: vertical-rl;
    font-size: 12px; color: var(--accent); white-space: nowrap; pointer-events: none;
  }
  .xlabel {
    text-shadow: 0 1px 6px var(--shadow-text);
    position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
    font-size: 12px; color: var(--accent); white-space: nowrap; pointer-events: none;
  }

  /* Map projection + overlay popover, anchored under its own button. Shares the
     .settings-pop corner, so the two are mutually exclusive (see showLayers). */
  /* graph's top-right corner — the rail owns the left edge, and the compare tray
     takes the graph's top-left */
  .map-tool {
    position: absolute; z-index: 6;
    top: var(--graph-top); right: var(--edge);
    font-size: 11px; padding: 5px 12px; border-radius: 8px;
    background: var(--panel-glass); border: 1px solid var(--b-12);
    color: var(--muted); cursor: pointer;
  }
  .map-tool.on { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 50%, transparent); }
  .layers-pop {
    top: calc(var(--graph-top) + 40px); right: var(--edge);
    width: 244px; z-index: 6;
    display: flex; flex-direction: column; gap: 14px; padding: 14px 16px;
    max-height: calc(100% - var(--graph-top) - 60px); overflow-y: auto;
  }
  .layers-pop .wide { width: 100%; margin-top: 2px; }
  .row-label { gap: 6px; font-size: 11px; color: var(--muted); }
  .check { flex-direction: row; align-items: center; gap: 6px; color: var(--fg); }
  .check.dis { opacity: 0.45; }
  .data-name {
    font-size: 11px; color: var(--fg); line-height: 1.3;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .muted { color: var(--muted); }

  .seg { display: flex; }
  .seg button { flex: 1; border-radius: 0; }
  .seg button:first-child { border-radius: 6px 0 0 6px; }
  .seg button:last-child { border-radius: 0 6px 6px 0; border-left: none; }
  .seg button.active { background: var(--accent); color: var(--on-accent); border-color: var(--accent); font-weight: 600; }
  .link { background: none; border: none; color: var(--accent); cursor: pointer; padding: 0; text-decoration: underline; }

  .small { font-size: 11px; }

  .gen-banner {
    /* centred on the GRAPH, not the window — the rail takes the left third */
    top: 76px; left: calc((var(--graph-left) + 100% - var(--edge)) / 2);
    transform: translateX(-50%); z-index: 5;
    display: flex; align-items: center; gap: 14px; padding: 8px 14px; font-size: 13px;
  }
  .gen-banner .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); margin-right: 7px; box-shadow: 0 0 8px var(--accent);
  }

  /* Pinned designs: the graph's top-RIGHT, directly under the ▤ layers button and
     sharing its right edge, so the two read as one column of map controls instead
     of one floating in each top corner. `.layers-pop` opens into the same slot and
     covers the tray while open — fine, it is a transient menu, and the two buttons
     that open them are mutually exclusive anyway. */
  .tray {
    top: calc(var(--graph-top) + 40px); right: var(--edge); width: 244px;
    max-height: calc(100% - var(--graph-top) - var(--edge) - 48px);
    overflow-y: auto; z-index: 3;
    display: flex; flex-direction: column; gap: 10px; padding: 12px 14px;
  }
  .tray-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--fg); font-weight: 600; }
  .card {
    border: 1px solid var(--b-08); border-left: 3px solid var(--pc);
    border-radius: 10px; padding: 8px 10px;
    background: var(--s-02);
  }
  .card:hover { background: var(--s-05); }
  .card-head { display: flex; align-items: center; gap: 7px; font-size: 12px; }
  .badge {
    width: 17px; height: 17px; border-radius: 50%; background: var(--pc);
    color: var(--on-accent); font-size: 10px; font-weight: 800; line-height: 17px; text-align: center; flex: none;
  }
  .card-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .x { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; padding: 0 2px; }
  .x:hover { color: var(--danger); }
  .card-body { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
  .card-actions { display: flex; flex-direction: column; gap: 6px; }
  .mini {
    font-size: 11px; padding: 3px 8px; border-radius: 6px;
  }
  .mini.active { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }
  .morph { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--rule); padding-top: 8px; }
  .morph-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .morph input[type="range"] { width: 100%; accent-color: var(--accent); }
  .morph-glyph { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .morph-cap { text-align: center; }

  /* Profiles rail: head · constraints block · rows · consequence strip. Only the
     rows flex — everything else is intrinsic, so the plot absorbs the slack. */
  .pc-panel {
    top: var(--graph-top); left: var(--edge); bottom: var(--edge); width: var(--rail-w);
    display: flex; flex-direction: column; gap: 6px;
    padding: 14px 16px 10px;
  }
  .pc-head {
    flex: none; height: 34px; overflow: hidden;
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px;
    font-size: 11.5px; color: var(--fg);
  }
  /* The rows are the point of this panel, so everything else is trimmed to the
     smallest fixed height that still holds its content, and .pc-body takes the
     rest. The fixed heights are load-bearing — see the note below. */
  .pc-body { flex: 1; min-height: 120px; }
  /* FIXED heights, not just `flex: none`. Anything here that grows when you
     select or constrain steals height from .pc-body, which resizes the canvas and
     makes every violin jump. Overflow is handled inside each block. */
  .vol-slot {
    flex: none; height: 38px; overflow: hidden; position: relative;
    border-bottom: 1px solid var(--rule); padding-bottom: 2px;
  }
  /* No rule above it: it belongs to the rows, and a full-width border made it
     look like a separate footer block. It is indented to the rows' label gutter
     instead, which ties it to them without drawing a line. */
  .lim-slot {
    flex: none; height: 38px; overflow-y: auto; overflow-x: hidden;
    padding-left: 2px;
  }
  .lim-slot { scrollbar-width: none; }
  .lim-slot::-webkit-scrollbar { display: none; }
  /* mark key, directly under the rows it describes */
  .pc-key {
    flex: none; height: 26px; overflow: hidden;
    display: flex; flex-wrap: nowrap; align-items: center;
    gap: 9px; font-size: 10px; color: var(--muted);
    border-top: 1px solid var(--rule); padding-top: 6px;
  }
  .pc-key .k { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; flex: none; }
  .pc-key svg { width: 18px; height: 10px; flex: none; overflow: visible; }

  .pc-key {
    flex: none; height: 26px; overflow: hidden;
    display: flex; flex-wrap: nowrap; align-items: center;
    gap: 9px; font-size: 10px; color: var(--muted);
    border-top: 1px solid var(--rule); padding-top: 6px;
  }
  .pc-key .k { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; flex: none; }
  .pc-key svg { width: 18px; height: 10px; flex: none; overflow: visible; }

  .pc-title { display: flex; align-items: center; gap: 5px; min-width: 0; }
  .pc-legend { display: flex; align-items: center; gap: 5px; color: var(--muted); }
  .pc-legend .ramp { width: 44px; height: 8px; }
  .ramp-ends { font-size: 10px; font-variant-numeric: tabular-nums; }
  /* the two view options share one line and take the head's full width, so the
     head settles at two lines instead of four in a 320px rail */
  .head-opts { flex-basis: 100%; display: flex; gap: 12px; }

  /* shown for the one round-trip that checks whether a stored session is still
     alive — without it the landing page flashes before the dataset comes back */
  .restoring {
    position: fixed; inset: 0;
    display: grid; place-items: center; color: var(--muted); font-size: 14px;
  }
  .overlay {
    position: absolute; inset: 0; z-index: 1;
    display: grid; place-items: center; color: var(--muted); font-size: 14px;
  }
  .overlay.err { color: var(--danger); }

  /* OPAQUE, both themes. This read `background: var(--on-accent)f2` — token
     concatenation, which is fragile (it only parses at all because both values
     happen to be 6-digit hex) and semantically wrong: --on-accent is the *text*
     colour for accent fills, so in dark mode the tooltip was a dark GREEN card at
     95%, sitting over a dense teal point cloud that showed straight through it.
     --panel-solid is the token that actually means "an opaque panel per theme". */
  .tooltip {
    box-shadow: var(--shadow-md);
    position: absolute; pointer-events: none; z-index: 50;
    background: var(--panel-solid); border: 1px solid var(--b-20); border-radius: 8px;
    padding: 8px 10px; font-size: 11px; min-width: 150px;
    backdrop-filter: blur(3px);   /* --panel-solid is 97/98%, not 100 */
  }
  .tt-title { color: var(--accent); font-weight: 600; margin-bottom: 4px; }
  .tt-row { display: flex; justify-content: space-between; gap: 14px; color: var(--fg); }
  .tt-row span:first-child { color: var(--muted); }
  .tt-row em { color: var(--muted); font-style: normal; font-size: 10px; }

  .ramp { height: 12px; border-radius: 3px; }
</style>
