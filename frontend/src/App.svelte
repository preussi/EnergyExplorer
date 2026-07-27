<script lang="ts">
  import {
    getMeta, getProjection, getColor, getSamples, getClusters, getExtremes, generate,
    getFlexibility, getVolume, getDependence, getShadowPairs,
    type Meta, type Projection, type ColorData, type SamplesData, type ClustersData,
    type GenerateResult, type ConstraintInput, type ExtremeDesign, type FlexRange,
    type VolumeEstimate, type Dependence, type DependenceMetric, type ShadowPair,
  } from "./lib/api";
  import { fly } from "svelte/transition";
  import { cubicOut } from "svelte/easing";
  import Landing from "./lib/Landing.svelte";
  import ScatterGL from "./lib/ScatterGL.svelte";
  import ParallelCoords from "./lib/ParallelCoords.svelte";
  import RadarGlyph from "./lib/RadarGlyph.svelte";
  import FacetView from "./lib/FacetView.svelte";
  import FlexBars from "./lib/FlexBars.svelte";
  import StarWheel from "./lib/StarWheel.svelte";
  import DependenceMatrix from "./lib/DependenceMatrix.svelte";

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

  // ---- view mode: projection map vs exact facet (shadow) views ----
  let viewMode = $state<"profiles" | "map" | "facets" | "coupling">("coupling");
  // pairwise dependence (dCor / MI / Pearson) for the Coupling tab + facet ranking
  let depData = $state<Dependence | null>(null);
  let depMetric = $state<DependenceMetric>("dcor");
  let shadowPairs = $state<ShadowPair[]>([]);   // boxiness per pair (for the dCor-vs-boxiness check)
  // a pair to open in the Facets tab (set by clicking a heatmap cell); one-shot,
  // FacetView consumes and clears it (see onconsumepair).
  let facetSel = $state<{ x: string; y: string } | null>(null);
  // whether the Coupling view's docked Facet panel is open (persists across the
  // one-shot facetSel so re-clicking cells doesn't close it). null = closed.
  let couplingPair = $state<{ x: string; y: string } | null>(null);

  // ---- star coordinates (user-steered linear projection) ----
  const isStar = $derived(method === "star");
  let normTech = $state<number[][] | null>(null); // 20k x 9, normalized tech values
  let starAnchors = $state<[number, number][]>([]);
  let touring = $state(false);
  let tourTimer: ReturnType<typeof setInterval> | null = null;
  let tourTarget: [number, number][] = [];

  async function ensureNormTech() {
    if (normTech || !meta) return;
    const s = sampler; // discard the response if the sampler changed mid-flight
    const tech = meta.axes; // all 9 axes are technologies
    try {
      const r = await getSamples(s, tech, "norm");
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
    color: string;
  }
  const PIN_COLORS = ["#7adfff", "#ff7ab2", "#ffd47a", "#b4ff7a", "#d49bff", "#ff9b7a"];
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
    const used = new Set(pins.map((p) => p.color));
    const color = PIN_COLORS.find((c) => !used.has(c)) ?? PIN_COLORS[pins.length % PIN_COLORS.length];
    pins = [...pins, {
      id: pinSeq++, label, values, point, normTech: normVals, color,
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
    exitSlice();
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
    sliceAxis = null;
    pcoords?.clearBrushes();
  }

  // ---- Phase 3: steering / generation ----
  let candidates = $state<GenerateResult | null>(null);
  let generating = $state(false);
  let genMsg = $state<string | null>(null);
  let manualConstraints = $state<ConstraintInput[]>([]);
  let brushConstraints = $state<{ axis: string; min: number; max: number }[]>([]);
  let genN = $state(2000);
  let newAxis = $state("nuclear");
  let newMin = $state<number | null>(null);
  let newMax = $state<number | null>(null);

  const allConstraints = $derived<ConstraintInput[]>([...manualConstraints, ...brushConstraints]);

  // slice/sweep state (logic defined below, after dispValues/dispFields exist)
  let sliceAxis = $state<string | null>(null);
  let sliceValue = $state(0);
  let sliceWidth = $state(0.03); // half-band as a fraction of the axis range
  function exitSlice() { sliceAxis = null; brushConstraints = []; }

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

  function addConstraint() {
    if (newMin == null && newMax == null) return;
    manualConstraints = [...manualConstraints, { axis: newAxis, min: newMin, max: newMax }];
    newMin = null; newMax = null;
  }
  function removeConstraint(i: number) {
    manualConstraints = manualConstraints.filter((_, j) => j !== i);
  }

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
    try { samples = await getSamples(sampler, meta.axes, "phys"); }
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
  // state that belonged to the previous dataset (pins, tour, constraints, slice…).
  async function reloadAll() {
    selected = null; candidates = null; genMsg = null;
    pins = []; stopMorph(); morphT = 0;
    if (touring) toggleTour();
    normTech = null; starAnchors = [];
    manualConstraints = []; brushConstraints = [];
    sliceAxis = null; volEstimate = null;
    facetSel = null; couplingPair = null;
    shadowPairs = []; depData = null; // force the dependence/shadow-pair effects to refetch
    try {
      const m = await getMeta();
      meta = m;
      dimEnabled = Object.fromEntries(m.axes.map((a) => [a, true])); // all on for a new dataset
      // an uploaded dataset may not carry the previously-selected axis names
      // ("" is the valid no-encoding choice, so leave it alone)
      if (field && !m.axes.includes(field)) field = m.axes[0];
      if (!m.axes.includes(newAxis)) newAxis = m.axes[0];
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
  function onLandingReady(m: Meta) {
    meta = m;
    started = true;
    reloadAll();
  }

  // ---- settings ----
  let showSettings = $state(false);
  // per-dimension on/off (keyed by axis name; missing/true = enabled). Disabled
  // dimensions are hidden from Profiles, Coupling, Facets, and the color/slice
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
  // If the color/slice axis gets disabled, fall back gracefully.
  $effect(() => {
    if (field && dimEnabled[field] === false) { field = ""; loadColor(); }
    if (sliceAxis && dimEnabled[sliceAxis] === false) exitSlice();
  });
  // Coupling view honours the toggles: subset the dependence matrices + shadow-pair
  // list to the active axes only (the DependenceMatrix seriates whatever it's given).
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
  const pairsView = $derived(shadowPairs.filter((p) => dimEnabled[p.x] !== false && dimEnabled[p.y] !== false));

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
  const dispNormTech = $derived(
    subIndex && normTech ? subIndex.map((r) => normTech![r]) : normTech,
  );
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
    return subIndex && v.length ? subIndex.map((r) => v[r]) : v;
  });

  // ---- slice / sweep (cut one axis, watch the other levers' densities & ranges
  // change) — the "slice the polytope" idea from the supervisor's whiteboard. ----
  // sampled extent of an axis (where the sample cloud actually is)
  function axisSpan(axis: string): { j: number; lo: number; hi: number } | null {
    const j = dispFields.indexOf(axis);
    if (j < 0 || !dispValues.length) return null;
    let lo = Infinity, hi = -Infinity;
    for (const row of dispValues) { const v = row[j]; if (v < lo) lo = v; if (v > hi) hi = v; }
    return { j, lo, hi };
  }
  // exact feasible extent (LP flexibility) — the polytope reaches further than the
  // samples do, so the slider spans this and marks the sampled sub-range on it.
  function feasSpan(axis: string): { j: number; lo: number; hi: number } | null {
    const j = dispFields.indexOf(axis);
    if (j < 0) return null;
    const rg = flexBase.find((r) => r.axis === axis);
    const s = axisSpan(axis);
    let lo = rg?.min ?? s?.lo ?? 0;
    let hi = rg?.max ?? s?.hi ?? 1;
    if (hi <= lo) hi = lo + 1;
    if (Math.abs(lo) < 1e-6 * (hi - lo)) lo = 0; // snap LP dust (−1e−12) to a clean 0
    return { j, lo, hi };
  }
  const sliceRange = $derived(sliceAxis ? feasSpan(sliceAxis) : null);      // slider bounds
  const sliceSampled = $derived(sliceAxis ? axisSpan(sliceAxis) : null);    // shaded sub-range
  function startSlice(axis: string | null) {
    simPin = null;
    if (!axis) { exitSlice(); selected = null; return; }
    // start in the middle of the *sampled* region so the first slice isn't empty
    const sp = axisSpan(axis) ?? feasSpan(axis);
    sliceAxis = axis;
    if (sp) sliceValue = (sp.lo + sp.hi) / 2;
  }
  const sliceBand = $derived.by(() => {
    if (!sliceAxis || !sliceRange) return null;
    const half = sliceWidth * (sliceRange.hi - sliceRange.lo);
    return { axis: sliceAxis, lo: sliceValue - half, hi: sliceValue + half };
  });
  // Sweeping the slice drives the selection (→ conditional violins on the other
  // axes) and a live constraint (→ exact flexibility / consequences of fixing it).
  $effect(() => {
    const band = sliceBand, r = sliceRange;
    if (!band || !r) return;
    const rows: number[] = [];
    for (let i = 0; i < dispValues.length; i++) {
      const v = dispValues[i][r.j];
      if (v >= band.lo && v <= band.hi) rows.push(i);
    }
    selected = rows;
    brushConstraints = [{ axis: band.axis, min: band.lo, max: band.hi }];
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
  // full-space per-technology ranges (stable reference for radar glyphs / kNN)
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
  const pcOverlayColor = $derived(
    hoverPin !== null && pins[hoverPin] ? pins[hoverPin].color : "#ffffff",
  );
  // The two interpolation endpoints, drawn statically in their pin colors on the
  // parallel coordinates while the white overlay sweeps between them.
  const pcStatic = $derived(
    pins.length >= 2
      ? [
          { values: pins[0].values, color: pins[0].color },
          { values: pins[1].values, color: pins[1].color },
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
  // Greedy seriation: start from the most-coupled pair, then repeatedly attach the
  // unused axis most coupled to either end of the growing chain.
  function couplingSeriation(D: number[][]): number[] {
    const n = D.length;
    if (n < 2) return Array.from({ length: n }, (_, i) => i);
    let bi = 0, bj = 1, best = -Infinity;
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++)
        if (D[i][j] > best) { best = D[i][j]; bi = i; bj = j; }
    const order = [bi, bj];
    const used = new Set(order);
    while (order.length < n) {
      const head = order[0], tail = order[order.length - 1];
      let node = -1, sim = -Infinity, atHead = false;
      for (let k = 0; k < n; k++) {
        if (used.has(k)) continue;
        if (D[tail][k] > sim) { sim = D[tail][k]; node = k; atHead = false; }
        if (D[head][k] > sim) { sim = D[head][k]; node = k; atHead = true; }
      }
      if (node < 0) break;
      used.add(node);
      if (atHead) order.unshift(node); else order.push(node);
    }
    return order;
  }
  // Permutation of dispFields column indices; [] means canonical order.
  const axisOrder = $derived.by(() => {
    if (!couplingAxes || !depData?.dcor?.length || !dispFields.length) return [];
    const idx = dispFields.map((f) => depData!.axes.indexOf(f));
    if (idx.some((i) => i < 0)) return [];
    const n = dispFields.length;
    const D = Array.from({ length: n }, (_, i) =>
      Array.from({ length: n }, (_, j) => depData!.dcor[idx[i]][idx[j]] ?? 0));
    return couplingSeriation(D);
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

  // ---- volume retained: how much of the near-optimal space survives the current
  // constraints. Cheap Monte-Carlo estimate = fraction of the uniform sample cloud
  // that falls in the constrained box (uniform samples → fraction ≈ volume ratio).
  // Wilson 95% interval for honesty; when 0 samples land the true volume is below
  // the sampling resolution (1/N), so we report an upper bound instead of "0".
  const volumeRetained = $derived.by(() => {
    const cons = allConstraints;
    const n = dispValues.length;
    if (!cons.length || !n) return null;
    const cols = cons
      .map((c) => ({ j: dispFields.indexOf(c.axis), min: c.min, max: c.max }))
      .filter((c) => c.j >= 0);
    if (!cols.length) return null;
    let k = 0;
    for (const row of dispValues) {
      let ok = true;
      for (const c of cols) {
        const v = row[c.j];
        if (c.min != null && v < c.min) { ok = false; break; }
        if (c.max != null && v > c.max) { ok = false; break; }
      }
      if (ok) k++;
    }
    const p = k / n;
    const z = 1.96, z2 = z * z; // Wilson score interval (well-behaved near 0)
    const denom = 1 + z2 / n;
    const center = (p + z2 / (2 * n)) / denom;
    const half = (z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)) / denom;
    return {
      k, n,
      pct: p * 100,
      lo: Math.max(0, center - half) * 100,
      hi: Math.min(1, center + half) * 100,
      resPct: (1 / n) * 100, // sampling resolution: nothing below this is countable
    };
  });

  // When the sample count gets too low to trust (deep in the tail), ask the backend
  // for a subset-simulation estimate — accurate where the raw fraction reads ~0.
  // Debounced + sequence-guarded like the flexibility fetch.
  let volEstimate = $state<VolumeEstimate | null>(null);
  let volBusy = $state(false);
  let volSeq = 0;
  let volTimer: ReturnType<typeof setTimeout> | undefined;
  const VOL_TRUST_K = 200; // ≥ this many samples in-region → the raw fraction is fine
  $effect(() => {
    const cons = allConstraints;
    const vr = volumeRetained;
    const my = ++volSeq;
    if (!cons.length || !vr || vr.k >= VOL_TRUST_K) {
      volEstimate = null; volBusy = false;
      return;
    }
    volBusy = true;
    volTimer = setTimeout(async () => {
      try {
        const r = await getVolume(cons);
        if (my === volSeq) volEstimate = r;
      } catch { if (my === volSeq) volEstimate = null; }
      finally { if (my === volSeq) volBusy = false; }
    }, 400);
    return () => { if (volTimer) clearTimeout(volTimer); };
  });
</script>

{#if !started}
  <Landing onready={onLandingReady} />
{:else}
<div class="stage" role="presentation" onmousemove={onMove}>
  <!-- top bar -->
  <header class="hud panel topbar">
    <div class="brand">
      <h1>Energy Explorer <small>near-optimal energy-system designs</small></h1>
      <button class="change-ds" title="pick or upload another dataset" onclick={() => (started = false)}>⟳ change dataset</button>
      <button class="change-ds" class:on={showSettings} title="settings"
              aria-label="settings" onclick={() => (showSettings = !showSettings)}>⚙ settings</button>
    </div>
    <div class="seg view-seg">
      <button class:active={viewMode === "coupling"} onclick={() => (viewMode = "coupling")}>Coupling</button>
      <button class:active={viewMode === "profiles"} onclick={() => (viewMode = "profiles")}>Profiles</button>
      <button class:active={viewMode === "facets"} onclick={() => (viewMode = "facets")}>Facets</button>
      <button class:active={viewMode === "map"} onclick={() => (viewMode = "map")}>Map</button>
    </div>
    {#if viewMode === "profiles"}
      <span class="topbar-hint">marginal distribution of each lever (violins) · brush an axis to filter · pinned designs & the A→B path overlay as lines</span>
    {:else if proj && viewMode === "map"}
      <span class="topbar-hint">
        {mode === "select" ? "drag to lasso-select" : "scroll to zoom · drag to pan · click a point to pin"}
        {#if isStar} · drag ◯ / pins to rotate the projection{/if}
        {#if !isMetric} · {method.toUpperCase()} axes are non-metric{/if}
      </span>
    {:else if viewMode === "facets"}
      <span class="topbar-hint">exact trade-off boundaries of the near-optimal space · brush below to constrain</span>
    {:else if viewMode === "coupling"}
      <span class="topbar-hint">nonlinear dependence between axes (distance correlation / mutual information) · click a cell → its facet</span>
    {/if}
  </header>

  {#if showSettings}
    <div class="settings-pop hud panel">
      <div class="settings-head">
        <span>Settings</span>
        <button class="cpl-close" aria-label="close settings" onclick={() => (showSettings = false)}>✕</button>
      </div>

      <div class="set-sect">
        <div class="set-label">
          <span>Dimensions</span>
          <span class="set-actions">
            <button class="link" onclick={() => setAllDims(true)} disabled={nActive === (meta?.axes.length ?? 0)}>all on</button>
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
        <span class="muted small">{nActive} of {meta?.axes.length ?? 0} on · shown in Profiles, Coupling & Facets. The Map (PCA) always uses all technologies.</span>
      </div>

      <div class="set-sect">
        <div class="set-label"><span>Map clusters</span></div>
        <label class="range-row">
          <input type="range" min="2" max="12" step="1" bind:value={clusterK} onchange={loadClusters} />
          <span class="range-val">{clusterK}</span>
        </label>
        <span class="muted small">number of k-means groups labelled on the Map.</span>
      </div>

      <div class="set-sect">
        <label class="check"><input type="checkbox" bind:checked={reduceMotion} /> Reduce motion</label>
        <span class="muted small">disable the facet slide-in and other animations.</span>
      </div>
    </div>
  {/if}

  <!-- borderless graph between the panels -->
  {#if viewMode === "coupling"}
    <div class="graph-region coupling-split" class:split={couplingPair}>
      <div class="cpl-matrix">
        <DependenceMatrix
          dep={depView}
          pairs={pairsView}
          metric={depMetric}
          onmetric={(m) => (depMetric = m)}
          onpair={(x, y) => { facetSel = { x, y }; couplingPair = { x, y }; }}
        />
      </div>
      {#if couplingPair}
        <div class="cpl-facet" transition:fly={{ x: 60, duration: reduceMotion ? 0 : 280, easing: cubicOut }}>
          <div class="cpl-facet-head">
            <span class="cpl-facet-title">exact facet · {short(couplingPair.x)} × {short(couplingPair.y)}</span>
            <button class="cpl-close" title="close facet panel" aria-label="close facet panel"
                    onclick={() => (couplingPair = null)}>✕</button>
          </div>
          {#if dispFields.length}
            <div class="cpl-facet-body">
              <FacetView
                fields={dispFields}
                values={dispValues}
                activeFields={activeAxes}
                colorValues={dispColorValues}
                colorCategorical={dispCategorical}
                colorMin={dispColorMin}
                colorMax={dispColorMax}
                constraints={allConstraints}
                selected={candidates ? null : selected}
                dependence={depView}
                selectPair={facetSel}
                onconsumepair={() => (facetSel = null)}
              />
            </div>
          {/if}
        </div>
      {/if}
    </div>
  {:else if viewMode === "facets"}
    <div class="graph-region">
      {#if dispFields.length}
        <FacetView
          fields={dispFields}
          values={dispValues}
          activeFields={activeAxes}
          colorValues={dispColorValues}
          colorCategorical={dispCategorical}
          colorMin={dispColorMin}
          colorMax={dispColorMax}
          constraints={allConstraints}
          selected={candidates ? null : selected}
          dependence={depView}
          selectPair={facetSel}
          onconsumepair={() => (facetSel = null)}
        />
      {/if}
    </div>
  {:else if viewMode === "map" && proj}
    <div class="graph-region">
      <ScatterGL
        bind:this={scatter}
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
        pins={pins.map((p, i) => ({ id: p.id, letter: String.fromCharCode(65 + i), point: pinPoints[i], color: p.color }))}
        path={morphPath}
        walkChains={inCandidates ? 0 : 4}
        walkActive={walkOn && !inCandidates}
        {selected}
        {mode}
        onhover={(i) => (hoverIdx = i)}
        onselect={(idx) => { exitSlice(); selected = idx.length ? idx : null; simPin = null; }}
        onpin={pinRow}
        onspoke={pinExtreme}
        draggableMarkers={isStar && !inCandidates}
        onmarkerdrag={onMarkerDrag}
      />
      <div class="ylabel">{axisLabel(1)}</div>
      <div class="xlabel">{axisLabel(0)}</div>
    </div>
  {/if}

  {#if inCandidates && candidates && (viewMode === "map" || viewMode === "profiles")}
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
        <div class="tt-row" class:cost={f === "net_present_cost"}>
          <span>{short(f)}</span><span>{fmt(dispValues[hoverRow][j])}</span>
        </div>
      {/each}
    </div>
  {/if}

  <!-- floating control panel (left) -->
  <aside class="hud panel">
    {#if viewMode === "map"}
      <label>Method
        <select bind:value={method} onchange={onMethodChange}>
          {#each meta?.methods ?? ["pca"] as m}<option value={m}>{m.toUpperCase()}</option>{/each}
          <option value="star">STAR ✦ (drag anchors)</option>
        </select>
      </label>
    {/if}

    {#if isStar && viewMode === "map"}
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
    <label>Color by
      <select bind:value={field} onchange={loadColor}>
        <option value="">— none —</option>
        {#each activeAxes as a}<option value={a}>{a}</option>{/each}
      </select>
    </label>

    <div class="sect">Slice</div>
    <label>Slice axis
      <select value={sliceAxis ?? ""} onchange={(e) => startSlice(e.currentTarget.value || null)}>
        <option value="">— off —</option>
        {#each activeAxes as a}<option value={a}>{short(a)}</option>{/each}
      </select>
    </label>
    {#if sliceAxis && sliceRange}
      {@const span = sliceRange.hi - sliceRange.lo}
      {@const sLo = sliceSampled ? Math.max(0, Math.min(100, ((sliceSampled.lo - sliceRange.lo) / span) * 100)) : 0}
      {@const sHi = sliceSampled ? Math.max(0, Math.min(100, ((sliceSampled.hi - sliceRange.lo) / span) * 100)) : 100}
      {@const inTail = !!sliceSampled && (sliceValue < sliceSampled.lo || sliceValue > sliceSampled.hi)}
      <div class="slice-track">
        <div class="slice-base"></div>
        <div class="slice-sampled" style="left:{sLo}%; right:{100 - sHi}%"></div>
        <input class="slice-slider" type="range"
          min={sliceRange.lo} max={sliceRange.hi} step={span / 200}
          bind:value={sliceValue} />
      </div>
      <div class="slice-info">
        {short(sliceAxis)} ≈ <strong>{fmt(sliceValue)}</strong>
        {#if inTail}<span class="slice-tail">· thin tail (no samples)</span>{/if}
      </div>
      <label class="slice-w">band ±{(sliceWidth * 100).toFixed(0)}%
        <input type="range" min="0.01" max="0.15" step="0.01" bind:value={sliceWidth} />
      </label>
      <span class="muted small">slider spans the full feasible range; the <span class="slice-legend">magenta</span> stretch is where designs are actually sampled · volume retained is under Consequences</span>
    {/if}

    {#if viewMode === "map"}
      <label class="check">
        <input type="checkbox" bind:checked={showOptimum} /> cost optimum ◯
      </label>
      <label class="check">
        <input type="checkbox" bind:checked={showClusters} /> region labels
      </label>

      <div class="sect">Overlays</div>
      <label class="check">
        <input type="checkbox" bind:checked={showDensity} /> option topography
      </label>
      <label class="check" class:dis={method !== "pca"}>
        <input type="checkbox" bind:checked={showCompass} disabled={method !== "pca"} /> tech compass
      </label>
      <label class="check" class:dis={method !== "pca" || inCandidates}>
        <input type="checkbox" bind:checked={showSpokes} disabled={method !== "pca" || inCandidates} /> extreme designs
      </label>
      <label class="check" class:dis={inCandidates}>
        <input type="checkbox" bind:checked={walkOn} disabled={inCandidates} /> sampler walk
      </label>

      <div class="mode">
        <span class="mode-label">Drag mode</span>
        <div class="seg">
          <button class:active={mode === "pan"} onclick={() => (mode = "pan")}>Pan</button>
          <button class:active={mode === "select"} onclick={() => (mode = "select")}>Select</button>
        </div>
      </div>
    {/if}

    <div class="sel-info">
      {#if selected}
        <span><strong>{selected.length.toLocaleString()}</strong> selected</span>
        <button class="link" onclick={clearSelection}>clear</button>
      {:else}
        <span class="muted">no selection</span>
      {/if}
    </div>

    {#if viewMode === "map"}
      <button onclick={() => scatter?.reset()}>Reset view</button>
    {/if}

    {#if consequences}
      <div class="sect">Consequences</div>
      {#if consequences.infeasible}
        <span class="cons-bad">✕ no feasible design under these constraints</span>
      {:else}
        {#if flexBusy}<span class="muted small">updating…</span>{/if}
        {#if volumeRetained}
          <div class="cons-vol">
            {#if volumeRetained.k >= 200}
              <!-- plenty of samples in-region: the raw fraction is reliable -->
              <span class="vol-num">volume retained ≈ {pct(volumeRetained.pct)}%</span>
              <span class="vol-ci">95% CI [{pct(volumeRetained.lo)}–{pct(volumeRetained.hi)}%] · {volumeRetained.k.toLocaleString()}/{volumeRetained.n.toLocaleString()} designs</span>
            {:else if volBusy && !volEstimate}
              <span class="vol-num">volume retained ≈ estimating…</span>
              <span class="vol-ci">too few samples land here — running subset simulation</span>
            {:else if volEstimate && volEstimate.feasible && volEstimate.ratio > 0}
              <span class="vol-num">volume retained ≈ {pct(volEstimate.ratio * 100)}%</span>
              <span class="vol-ci">
                ±{Math.round(volEstimate.cv * 100)}% ({volEstimate.method === "subset_simulation" ? `subset sim, ${volEstimate.levels} levels` : "sample estimate"}) ·
                {volumeRetained.k.toLocaleString()}/{volumeRetained.n.toLocaleString()} sampled here
              </span>
            {:else if volEstimate && !volEstimate.feasible}
              <span class="vol-num">volume retained ≈ 0%</span>
              <span class="vol-ci">region is empty (or lower-dimensional)</span>
            {:else}
              <span class="vol-num">volume retained &lt; {pct(volumeRetained.resPct)}%</span>
              <span class="vol-ci">below sampling resolution ({volumeRetained.n.toLocaleString()} samples)</span>
            {/if}
          </div>
        {/if}
        {#each consequences.items as it}
          <div class="cons-item {it.kind}">{it.text}</div>
        {/each}
        {#if !consequences.items.length}
          <span class="muted small">no lever forced or capped yet</span>
        {/if}
        {#if volumeRetained && volumeRetained.k < 200}
          <span class="cons-note">the region stays feasible — the flexibility figures below are exact (LP on the polytope), independent of how many samples land here.</span>
        {/if}
      {/if}
    {/if}

    <div class="sect">Remaining flexibility</div>
    {#if flexBase.length && meta}
      <FlexBars
        base={flexBase}
        current={flexCur.length ? flexCur : flexBase}
        optimum={meta.optimum.u_star}
        feasible={flexFeasible}
        busy={flexBusy}
      />
      <span class="muted small">exact feasible range per lever under your constraints (LP, not sample filtering)</span>
    {:else}
      <span class="muted small">computing ranges…</span>
    {/if}

    <div class="sect">Generate designs</div>
    <div class="gen">
      <div class="cons-add">
        <select bind:value={newAxis}>
          {#each meta?.axes ?? [] as a}<option value={a}>{short(a)}</option>{/each}
        </select>
        <div class="cons-row">
          <input type="number" placeholder="min" bind:value={newMin} />
          <input type="number" placeholder="max" bind:value={newMax} />
          <button class="add" onclick={addConstraint} title="add constraint">+</button>
        </div>
      </div>

      {#if manualConstraints.length || brushConstraints.length}
        <div class="chips">
          {#each manualConstraints as c, i}
            <span class="chip">
              {short(c.axis)}{c.min != null ? " ≥ " + fmt(c.min) : ""}{c.max != null ? " ≤ " + fmt(c.max) : ""}
              <button onclick={() => removeConstraint(i)}>×</button>
            </span>
          {/each}
          {#each brushConstraints as c}
            <span class="chip brush">{short(c.axis)} ∈ [{fmt(c.min)}, {fmt(c.max)}]</span>
          {/each}
        </div>
      {:else}
        <span class="muted small">no constraints · brush an axis or add one above</span>
      {/if}

      <label class="n-row">designs
        <input type="number" min="200" max="5000" step="100" bind:value={genN} />
      </label>
      <button class="primary" onclick={generateNow} disabled={generating}>
        {generating ? "Generating…" : "Generate"}
      </button>
      {#if genMsg}<span class="err-msg">{genMsg}</span>{/if}
    </div>

    <div class="info">
      {#if meta}<p><strong>{Math.min(DISPLAY_N, meta.n_samples).toLocaleString()}</strong> of {meta.n_samples.toLocaleString()} samples shown · {meta.axes.length} dims</p>{/if}
      {#if proj}<p class="muted">{proj.cached ? "cached" : "live"} {proj.method.toUpperCase()} · optimum {proj.optimum ? "shown" : "n/a"}</p>{/if}
    </div>

    {#if colorData}
      <div class="legend">
        <span class="legend-title">{colorData.field}</span>
        <div class="ramp"></div>
        <div class="ramp-labels"><span>{fmt(colorData.min)}</span><span>{fmt(colorData.max)}</span></div>
      </div>
    {/if}

    <div class="sect">Dataset</div>
    <div class="data-box">
      <div class="data-name" title={meta?.dataset.name}>{meta?.dataset.name ?? "—"}</div>
      <span class="muted small">use “⟳ change dataset” (top bar) to pick or upload another polytope</span>
    </div>
  </aside>

  <!-- compare tray (right): pinned designs, radar glyphs, A→B morph -->
  {#if (viewMode === "map" || viewMode === "profiles") && (pins.length || proj?.optimum)}
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
          style="--pc:{p.color}"
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
              color={p.color}
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
                color="#ffffff"
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

  <!-- parallel-coordinates panel: bottom strip elsewhere, full stage in Profiles -->
  {#if dispValues.length}
    <section class="hud panel pc-panel" class:full={viewMode === "profiles"}>
      <div class="pc-head">
        <span>
          Parallel coordinates · {dispFields.length} axes · colored by {short(field)}
          {#if inCandidates}<span class="muted">· generated candidates</span>{/if}
        </span>
        <label class="check small" title="order axes so strongly-coupled ones (distance correlation) are adjacent">
          <input type="checkbox" bind:checked={couplingAxes} /> order by coupling
        </label>
        <span class="muted">drag along an axis to filter · click an axis to clear it</span>
      </div>
      <div class="pc-body">
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
          slice={sliceBand}
          {selected}
          onbrush={(rows, cons) => { exitSlice(); selected = rows; brushConstraints = cons; simPin = null; }}
        />
      </div>
    </section>
  {/if}
</div>
{/if}

<style>
  .stage { position: relative; flex: 1; min-height: 0; overflow: hidden; }

  .hud { position: absolute; z-index: 2; }
  .panel {
    background: rgba(16, 21, 28, 0.72);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 16px;
    box-shadow: 0 18px 44px rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(10px);
  }

  .topbar {
    top: 16px; left: 16px; right: 16px; height: 48px; z-index: 4;
    display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 16px;
    padding: 0 18px;
  }
  /* center column keeps the view selector fixed regardless of the side text widths */
  .view-seg { flex: none; justify-self: center; }
  .view-seg button { padding: 4px 16px; font-size: 12px; }
  .star-box {
    display: flex; justify-content: center; padding: 4px 0;
    border: 1px dashed rgba(45, 212, 191, 0.25); border-radius: 12px;
  }
  h1 { font-size: 18px; margin: 0; font-weight: 600; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
  .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .change-ds {
    flex: none; font-size: 11px; padding: 3px 9px; border-radius: 7px; white-space: nowrap;
    background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.12);
    color: var(--muted); cursor: pointer;
  }
  .change-ds:hover { background: rgba(255, 255, 255, 0.1); color: var(--fg); border-color: var(--accent); }
  .change-ds.on { border-color: var(--accent); color: var(--accent); }

  /* settings popover, anchored under the top bar (left) */
  .settings-pop {
    top: 76px; left: 16px; width: 244px; z-index: 6;
    display: flex; flex-direction: column; gap: 14px; padding: 14px 16px;
    max-height: calc(100% - 96px); overflow-y: auto;
  }
  .settings-head {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 13px; color: var(--fg);
  }
  .set-sect { display: flex; flex-direction: column; gap: 6px;
    border-top: 1px solid #2a3441; padding-top: 12px; }
  .set-sect:first-of-type { border-top: none; padding-top: 0; }
  .set-label { display: flex; align-items: center; justify-content: space-between;
    font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); }
  .set-actions { display: flex; gap: 8px; }
  .check.dim { text-transform: capitalize; }
  .range-row { flex-direction: row; align-items: center; gap: 10px; }
  .range-row input { flex: 1; accent-color: var(--accent); }
  .range-val { font-size: 12px; color: var(--fg); min-width: 16px; text-align: right; }
  .topbar-hint { color: var(--muted); font-size: 12px; text-align: right; min-width: 0; line-height: 1.15; }

  .graph-region {
    position: absolute; z-index: 1;
    top: 80px; left: 252px; right: 16px; bottom: 232px;
  }
  /* Coupling view: matrix alone by default; when a cell is clicked the facet
     panel docks into the right half and the matrix keeps the left half. */
  .coupling-split { display: flex; gap: 14px; min-height: 0; overflow: hidden; }
  .coupling-split .cpl-matrix { flex: 1 1 100%; min-width: 0; min-height: 0; }
  .coupling-split.split .cpl-matrix { flex: 1 1 50%; }
  /* facet panel: a header row (title + close) above the FacetView, so the close
     button never overlaps FacetView's own controls (e.g. the rank dropdown). */
  .cpl-facet {
    flex: 1 1 50%; min-width: 0; min-height: 0;
    display: flex; flex-direction: column; gap: 6px;
    border-left: 1px solid rgba(255, 255, 255, 0.08); padding-left: 14px;
  }
  .cpl-facet-head { flex: none; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .cpl-facet-title {
    font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .cpl-facet-body { flex: 1; min-height: 0; position: relative; }
  .cpl-close {
    flex: none; display: flex; align-items: center; justify-content: center;
    width: 22px; height: 22px; border-radius: 6px; padding: 0; font-size: 12px; line-height: 1;
    background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.12);
    color: var(--muted); cursor: pointer;
  }
  .cpl-close:hover { background: rgba(255, 255, 255, 0.1); color: var(--fg); }
  .ylabel {
    position: absolute; left: 0; top: 50%;
    transform: translateY(-50%) rotate(180deg); writing-mode: vertical-rl;
    font-size: 12px; color: var(--accent); white-space: nowrap; pointer-events: none;
    text-shadow: 0 1px 6px rgba(0, 0, 0, 0.95);
  }
  .xlabel {
    position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
    font-size: 12px; color: var(--accent); white-space: nowrap; pointer-events: none;
    text-shadow: 0 1px 6px rgba(0, 0, 0, 0.95);
  }

  aside.panel {
    top: 80px; left: 16px; bottom: 16px; width: 220px;
    display: flex; flex-direction: column; gap: 12px; overflow-y: auto;
    padding: 16px;
  }
  .sect {
    font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--muted); border-top: 1px solid #2a3441; padding-top: 10px; margin-top: 2px;
  }
  .check { flex-direction: row; align-items: center; gap: 6px; color: var(--fg); }
  .check.dis { opacity: 0.45; }
  /* slice / sweep */
  /* slice sweep slider spans the feasible range; the sampled sub-range is shaded */
  .slice-track { position: relative; height: 18px; display: flex; align-items: center; }
  .slice-base { position: absolute; left: 0; right: 0; height: 4px; border-radius: 2px; background: #2a3441; }
  .slice-sampled { position: absolute; height: 4px; border-radius: 2px; background: rgba(232, 121, 249, 0.5); }
  .slice-slider {
    position: relative; z-index: 1; width: 100%; margin: 0;
    -webkit-appearance: none; appearance: none; background: transparent;
  }
  .slice-slider::-webkit-slider-runnable-track { height: 4px; background: transparent; }
  .slice-slider::-moz-range-track { height: 4px; background: transparent; }
  .slice-slider::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 12px; height: 12px; border-radius: 50%; background: #e879f9;
    margin-top: -4px; cursor: pointer; box-shadow: 0 0 0 2px rgba(10, 14, 20, 0.6);
  }
  .slice-slider::-moz-range-thumb {
    width: 12px; height: 12px; border: none; border-radius: 50%; background: #e879f9; cursor: pointer;
  }
  .slice-info { font-size: 12px; color: var(--fg); }
  .slice-info strong { color: #e879f9; }
  .slice-tail { color: #f0a14e; font-size: 11px; }
  .slice-legend { color: #e879f9; }
  .slice-w { font-size: 11px; color: var(--muted); }
  .slice-w input { width: 100%; accent-color: #e879f9; }
  /* dataset readout */
  .data-box { display: flex; flex-direction: column; gap: 6px; }
  .data-name {
    font-size: 11px; color: var(--fg); line-height: 1.3;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  /* consequences (§4) */
  .cons-vol {
    display: flex; flex-direction: column; gap: 1px;
    padding: 4px 8px; border-radius: 6px;
    background: rgba(45, 212, 191, 0.07); border-left: 3px solid var(--accent);
  }
  .vol-num { font-size: 13px; color: var(--fg); font-weight: 600; }
  .vol-ci { font-size: 10px; color: var(--muted); }
  .cons-item {
    font-size: 12px; padding: 3px 8px; border-radius: 6px; border-left: 3px solid;
    background: rgba(255, 255, 255, 0.03);
  }
  .cons-item.force { border-color: #f4b43c; }
  .cons-item.include { border-color: #e64a3b; }
  .cons-item.limit { border-color: #2e8cdc; }
  .cons-bad { color: #e64a3b; font-size: 13px; }
  .cons-note {
    font-size: 11px; color: var(--muted); font-style: italic; line-height: 1.3;
    border-left: 3px solid rgba(45, 212, 191, 0.5); padding: 2px 8px;
  }
  .info { margin-top: 4px; font-size: 13px; }
  .info p { margin: 4px 0; }
  .muted { color: var(--muted); }

  .mode { display: flex; flex-direction: column; gap: 4px; }
  .mode-label { font-size: 12px; color: var(--muted); }
  .seg { display: flex; }
  .seg button { flex: 1; border-radius: 0; }
  .seg button:first-child { border-radius: 6px 0 0 6px; }
  .seg button:last-child { border-radius: 0 6px 6px 0; border-left: none; }
  .seg button.active { background: var(--accent); color: #06241f; border-color: var(--accent); font-weight: 600; }
  .sel-info { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .link { background: none; border: none; color: var(--accent); cursor: pointer; padding: 0; text-decoration: underline; }

  .gen { display: flex; flex-direction: column; gap: 8px; }
  .cons-add { display: flex; flex-direction: column; gap: 6px; }
  .cons-row { display: flex; gap: 6px; }
  .cons-row input { width: 100%; min-width: 0; }
  .cons-row .add { flex: none; width: 32px; padding: 0; font-size: 16px; }
  input[type="number"] {
    background: var(--panel); color: var(--fg); border: 1px solid #30363d;
    border-radius: 6px; padding: 6px 8px; font-size: 13px; width: 100%;
  }
  .n-row { flex-direction: row; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; }
  .n-row input { width: 78px; }
  .chips { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip {
    display: inline-flex; align-items: center; gap: 5px; font-size: 11px;
    background: #20303a; border: 1px solid #2f4a52; color: var(--fg);
    border-radius: 999px; padding: 2px 4px 2px 8px;
  }
  .chip.brush { background: #1b2a33; border-style: dashed; color: var(--muted); padding-right: 8px; }
  .chip button { background: none; border: none; color: var(--muted); cursor: pointer; padding: 0 2px; font-size: 13px; line-height: 1; }
  .chip button:hover { color: #f85149; }
  .primary { background: var(--accent); color: #06241f; border-color: var(--accent); font-weight: 600; }
  .primary:disabled { opacity: 0.6; cursor: default; }
  .err-msg { color: #f0a14e; font-size: 11px; }
  .small { font-size: 11px; }

  .gen-banner {
    top: 76px; left: 50%; transform: translateX(-50%); z-index: 5;
    display: flex; align-items: center; gap: 14px; padding: 8px 14px; font-size: 13px;
  }
  .gen-banner .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent); margin-right: 7px; box-shadow: 0 0 8px var(--accent);
  }

  /* compare tray */
  .tray {
    top: 80px; right: 16px; width: 232px; max-height: calc(100% - 330px);
    overflow-y: auto; z-index: 3;
    display: flex; flex-direction: column; gap: 10px; padding: 12px 14px;
  }
  .tray-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--fg); font-weight: 600; }
  .card {
    border: 1px solid rgba(255, 255, 255, 0.08); border-left: 3px solid var(--pc);
    border-radius: 10px; padding: 8px 10px;
    background: rgba(255, 255, 255, 0.02);
  }
  .card:hover { background: rgba(255, 255, 255, 0.05); }
  .card-head { display: flex; align-items: center; gap: 7px; font-size: 12px; }
  .badge {
    width: 17px; height: 17px; border-radius: 50%; background: var(--pc);
    color: #0a0e14; font-size: 10px; font-weight: 800; line-height: 17px; text-align: center; flex: none;
  }
  .card-label { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .x { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; padding: 0 2px; }
  .x:hover { color: #f85149; }
  .card-body { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
  .card-actions { display: flex; flex-direction: column; gap: 6px; }
  .mini {
    font-size: 11px; padding: 3px 8px; border-radius: 6px;
  }
  .mini.active { background: var(--accent); color: #0b0f14; border-color: var(--accent); }
  .morph { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid #2a3441; padding-top: 8px; }
  .morph-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .morph input[type="range"] { width: 100%; accent-color: var(--accent); }
  .morph-glyph { display: flex; flex-direction: column; align-items: center; gap: 2px; }
  .morph-cap { text-align: center; }

  .pc-panel {
    left: 252px; right: 16px; bottom: 16px; height: 200px;
    display: flex; flex-direction: column; gap: 6px;
    padding: 12px 16px;
  }
  /* Profiles view: parallel coords fill the main stage (leaving the left controls
     and the right compare tray their gutters). */
  .pc-panel.full {
    top: 80px; right: 264px; bottom: 16px; height: auto;
  }
  .pc-head { display: flex; justify-content: space-between; gap: 12px; font-size: 12px; color: var(--fg); }
  .pc-body { flex: 1; min-height: 0; }

  .overlay {
    position: absolute; inset: 0; z-index: 1;
    display: grid; place-items: center; color: var(--muted); font-size: 14px;
  }
  .overlay.err { color: #f85149; }

  .tooltip {
    position: absolute; pointer-events: none; z-index: 50;
    background: #0b0f14f2; border: 1px solid #30363d; border-radius: 8px;
    padding: 8px 10px; font-size: 11px; min-width: 150px;
    box-shadow: 0 8px 22px rgba(0, 0, 0, 0.6);
  }
  .tt-title { color: var(--accent); font-weight: 600; margin-bottom: 4px; }
  .tt-row { display: flex; justify-content: space-between; gap: 14px; color: var(--fg); }
  .tt-row span:first-child { color: var(--muted); }
  .tt-row.cost { margin-top: 4px; padding-top: 4px; border-top: 1px solid #30363d; font-weight: 600; }

  .legend { margin-top: 6px; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }
  .legend-title { color: var(--muted); }
  .ramp { height: 12px; border-radius: 3px; background: linear-gradient(90deg, #45337a, #3a51a8, #228c8c, #5fbf61, #fce824); }
  .ramp-labels { display: flex; justify-content: space-between; color: var(--muted); }
</style>
