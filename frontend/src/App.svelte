<script lang="ts">
  import {
    getMeta, getProjection, getColor, getSamples, getClusters, getExtremes, generate,
    getFlexibility,
    type Meta, type Projection, type ColorData, type SamplesData, type ClustersData,
    type GenerateResult, type ConstraintInput, type ExtremeDesign, type FlexRange,
  } from "./lib/api";
  import ScatterGL from "./lib/ScatterGL.svelte";
  import ParallelCoords from "./lib/ParallelCoords.svelte";
  import RadarGlyph from "./lib/RadarGlyph.svelte";
  import FacetView from "./lib/FacetView.svelte";
  import FlexBars from "./lib/FlexBars.svelte";
  import StarWheel from "./lib/StarWheel.svelte";

  let meta = $state<Meta | null>(null);
  let proj = $state<Projection | null>(null);
  let colorData = $state<ColorData | null>(null);
  let samples = $state<SamplesData | null>(null);
  let clustersData = $state<ClustersData | null>(null);
  let extremesData = $state<ExtremeDesign[]>([]);

  let method = $state("pca");
  let sampler = $state("chrrt");
  let field = $state("net_present_cost");
  let space = $state("phys");
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
  let viewMode = $state<"map" | "facets">("map");

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
    const tech = meta.axes.filter((a) => a !== meta!.cost_axis);
    try {
      const r = await getSamples(s, tech, "norm");
      if (s === sampler) normTech = r.values;
    } catch (e) { error = (e as Error).message; }
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
  function toggleTour() {
    if (touring) {
      touring = false;
      if (tourTimer) { clearInterval(tourTimer); tourTimer = null; }
      return;
    }
    touring = true;
    tourTarget = randomFrame();
    tourTimer = setInterval(() => {
      let maxd = 0;
      starAnchors = starAnchors.map((a, j) => {
        const t = tourTarget[j] ?? a;
        const nx = a[0] + (t[0] - a[0]) * 0.045;
        const ny = a[1] + (t[1] - a[1]) * 0.045;
        maxd = Math.max(maxd, Math.hypot(t[0] - nx, t[1] - ny));
        return [nx, ny] as [number, number];
      });
      if (maxd < 0.02) tourTarget = randomFrame();
    }, 50);
  }
  // initialize anchors the first time star mode is ready (circle layout: every
  // handle is grabbable; PCA loadings would bunch most anchors at the center)
  $effect(() => {
    if (isStar && starAnchors.length === 0 && meta) {
      starAnchors = circleAnchors();
    }
  });
  const starPoints = $derived.by(() => {
    if (!isStar || !normTech || !starAnchors.length) return [];
    const A = starAnchors;
    return normTech.map((row) => {
      let x = 0, y = 0;
      for (let j = 0; j < A.length; j++) { x += row[j] * A[j][0]; y += row[j] * A[j][1]; }
      return [x, y];
    });
  });
  const starOptimum = $derived.by(() => {
    if (!isStar || !meta || !starAnchors.length) return null;
    const costIdxMeta = meta.axes.indexOf(meta.cost_axis);
    const tech = meta.optimum.norm.filter((_, k) => k !== costIdxMeta);
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

  function addPin(label: string, values: number[], point: [number, number], normVals?: number[]) {
    if (pins.length >= 6) return;
    if (pins.some((p) => p.label === label)) return;
    pins = [...pins, {
      id: pinSeq++, label, values, point, normTech: normVals,
      color: PIN_COLORS[pins.length % PIN_COLORS.length],
    }];
    morphT = 0;
  }
  function pinRow(i: number) {
    if (!dispValues[i] || !dispPoints[i]) return;
    addPin(
      `${inCandidates ? "cand" : "#"}${i}`,
      dispValues[i],
      dispPoints[i] as [number, number],
      !inCandidates ? normTech?.[i] : undefined,
    );
  }
  function pinOptimum() {
    if (!dispOptimum || !meta) return;
    const v = [...meta.optimum.u_star, meta.optimum.c_star];
    const ci = meta.axes.indexOf(meta.cost_axis);
    addPin("u* optimum", v, dispOptimum as [number, number],
      meta.optimum.norm.filter((_, k) => k !== ci));
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
    if (!dispValues.length || !techRanges.length) return;
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

  const PALETTE = ["#e64a3b", "#2e8cdc", "#2ebd78", "#f2c52e", "#9c59b6", "#e67d21"];
  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;

  // star mode is computed client-side on top of the PCA fetch (for loadings/optimum)
  const fetchMethod = $derived(method === "star" ? "pca" : method);

  async function loadProjection() {
    loading = true; error = null;
    try { proj = await getProjection(fetchMethod, sampler, 2); }
    catch (e) { error = (e as Error).message; proj = null; }
    finally { loading = false; }
  }
  async function loadColor() {
    try { colorData = await getColor(sampler, field, space); }
    catch (e) { error = (e as Error).message; }
  }
  async function loadSamples() {
    if (!meta) return;
    try { samples = await getSamples(sampler, meta.axes, "phys"); }
    catch (e) { error = (e as Error).message; }
  }
  async function loadClusters() {
    try { clustersData = await getClusters(fetchMethod, sampler, 6); }
    catch { clustersData = null; }
  }
  async function loadExtremes() {
    try { extremesData = (await getExtremes(sampler)).extremes; }
    catch { extremesData = []; }
  }

  function onSamplerOrMethod() {
    selected = null;
    candidates = null; genMsg = null;
    pins = []; stopMorph(); morphT = 0; // pin coords live in the previous projection space
    if (touring) toggleTour();
    normTech = null; starAnchors = []; // sampler may have changed; refetch/reinit lazily
    if (method === "star") ensureNormTech();
    loadProjection(); loadColor(); loadSamples(); loadClusters(); loadExtremes();
  }

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

  $effect(() => {
    getMeta().then((m) => {
      meta = m; loadProjection(); loadColor(); loadSamples(); loadClusters(); loadExtremes();
      getFlexibility([]).then((r) => {
        flexBase = r.ranges;
        // don't clobber a constrained result that may have landed meanwhile
        if (!allConstraints.length) flexCur = r.ranges;
      }).catch(() => {});
    }).catch((e) => (error = (e as Error).message));
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
  const dispPoints = $derived(
    candidates ? candidates.points : isStar ? starPoints : (proj?.points ?? []),
  );
  const dispOptimum = $derived(
    !candidates && isStar ? starOptimum : (proj?.optimum ?? null),
  );
  const dispFields = $derived(candidates ? candidates.fields : (samples?.fields ?? []));
  const dispValues = $derived(candidates ? candidates.values : (samples?.values ?? []));
  const colorIdx = $derived(
    dispFields.indexOf(field === "chain" ? "net_present_cost" : field),
  );
  const dispColorValues = $derived.by(() => {
    if (!candidates) return colorData?.values ?? [];
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
  const costIdx = $derived(dispFields.indexOf("net_present_cost"));
  const techValues = (p: Pin) =>
    p.values.filter((_, j) => dispFields[j] !== "net_present_cost");

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

  // ---- hover tooltip ----
  const hoverRow = $derived(
    hoverIdx === null ? null
    : candidates ? hoverIdx
    : proj ? proj.index[hoverIdx] : null,
  );
  function onMove(e: MouseEvent) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    mouse = [e.clientX - r.left, e.clientY - r.top];
  }
  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(3);
  const deltaCost = (p: Pin) => {
    if (!meta || costIdx < 0) return "";
    const d = ((p.values[costIdx] - meta.optimum.c_star) / meta.optimum.c_star) * 100;
    return `${d >= 0 ? "+" : ""}${d.toFixed(1)}%`;
  };
</script>

<div class="stage" role="presentation" onmousemove={onMove}>
  <!-- top bar -->
  <header class="hud panel topbar">
    <h1>Energy Explorer <small>near-optimal energy-system designs</small></h1>
    <div class="seg view-seg">
      <button class:active={viewMode === "map"} onclick={() => (viewMode = "map")}>Map</button>
      <button class:active={viewMode === "facets"} onclick={() => (viewMode = "facets")}>Facets</button>
    </div>
    {#if proj && viewMode === "map"}
      <span class="topbar-hint">
        {mode === "select" ? "drag to lasso-select" : "scroll to zoom · drag to pan · click a point to pin"}
        {#if !isMetric} · {method.toUpperCase()} axes are non-metric{/if}
      </span>
    {:else if viewMode === "facets"}
      <span class="topbar-hint">exact trade-off boundaries of the near-optimal space · brush below to constrain</span>
    {/if}
  </header>

  <!-- borderless graph between the panels -->
  {#if viewMode === "facets"}
    <div class="graph-region">
      {#if samples}
        <FacetView
          fields={samples.fields}
          values={samples.values}
          colorValues={colorData?.values ?? []}
          colorCategorical={colorData?.categorical ?? false}
          colorMin={colorData?.min ?? 0}
          colorMax={colorData?.max ?? 1}
          constraints={allConstraints}
          selected={candidates ? null : selected}
        />
      {/if}
    </div>
  {:else if proj && colorData}
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
        onselect={(idx) => (selected = idx.length ? idx : null)}
        onpin={pinRow}
        onspoke={pinExtreme}
      />
      <div class="ylabel">{axisLabel(1)}</div>
      <div class="xlabel">{axisLabel(0)}</div>
    </div>
  {/if}

  {#if inCandidates && candidates && viewMode === "map"}
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
      <div class="tt-title">{inCandidates ? "candidate" : "design"} #{hoverRow}</div>
      {#each dispFields as f, j}
        <div class="tt-row" class:cost={f === "net_present_cost"}>
          <span>{short(f)}</span><span>{fmt(dispValues[hoverRow][j])}</span>
        </div>
      {/each}
    </div>
  {/if}

  <!-- floating control panel (left) -->
  <aside class="hud panel">
    <label>Sampler
      <select bind:value={sampler} onchange={onSamplerOrMethod}>
        {#each meta?.samplers ?? ["chrrt", "har"] as s}<option value={s}>{s}</option>{/each}
      </select>
    </label>
    <label>Method
      <select bind:value={method} onchange={onSamplerOrMethod}>
        {#each meta?.methods ?? ["pca"] as m}<option value={m}>{m.toUpperCase()}</option>{/each}
        <option value="star">STAR ✦ (drag anchors)</option>
      </select>
    </label>

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
        {#each meta?.axes ?? [] as a}<option value={a}>{a}</option>{/each}
        <option value="chain">chain (sampler diagnostic)</option>
      </select>
    </label>
    <label>Units (for color)
      <select bind:value={space} onchange={loadColor} disabled={field === "chain"}>
        <option value="phys">physical</option>
        <option value="norm">normalized</option>
      </select>
    </label>
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

    <div class="sel-info">
      {#if selected}
        <span><strong>{selected.length.toLocaleString()}</strong> selected</span>
        <button class="link" onclick={clearSelection}>clear</button>
      {:else}
        <span class="muted">no selection</span>
      {/if}
    </div>

    <button onclick={() => scatter?.reset()}>Reset view</button>

    <div class="sect">Remaining flexibility</div>
    {#if flexBase.length && meta}
      <FlexBars
        base={flexBase}
        current={flexCur.length ? flexCur : flexBase}
        optimum={[...meta.optimum.u_star, meta.optimum.c_star]}
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
      {#if meta}<p><strong>{meta.n_samples.toLocaleString()}</strong> samples · {meta.axes.length} dims</p>{/if}
      {#if proj}<p class="muted">{proj.cached ? "cached" : "live"} {proj.method.toUpperCase()} · optimum {proj.optimum ? "shown" : "n/a"}</p>{/if}
    </div>

    {#if colorData}
      <div class="legend">
        <span class="legend-title">{colorData.field}</span>
        {#if colorData.categorical}
          {#each Array(colorData.max + 1) as _, i}
            <div class="swatch"><span style="background:{PALETTE[i % PALETTE.length]}"></span>chain {i}</div>
          {/each}
        {:else}
          <div class="ramp"></div>
          <div class="ramp-labels"><span>{fmt(colorData.min)}</span><span>{fmt(colorData.max)}</span></div>
        {/if}
      </div>
    {/if}
  </aside>

  <!-- compare tray (right): pinned designs, radar glyphs, A→B morph (map view only) -->
  {#if viewMode === "map" && (pins.length || proj?.optimum)}
    <section class="hud panel tray">
      <div class="tray-head">
        <span>Pinned designs</span>
        <button class="link" onclick={pinOptimum} title="pin the cost optimum">+ u*</button>
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
            <span class="card-cost" title="cost vs optimum">{deltaCost(p)}</span>
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
              <button class="mini" onclick={() => findSimilar(p)}>similar</button>
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
          <span class="muted small">every point on this path is a feasible near-optimal design (convexity ✓)</span>
        </div>
      {/if}
    </section>
  {/if}

  <!-- floating parallel-coordinates panel (bottom) -->
  {#if dispValues.length}
    <section class="hud panel pc-panel">
      <div class="pc-head">
        <span>
          Parallel coordinates · {dispFields.length} axes · colored by {short(field)}
          {#if inCandidates}<span class="muted">· generated candidates</span>{/if}
        </span>
        <span class="muted">drag along an axis to filter · click an axis to clear it</span>
      </div>
      <div class="pc-body">
        <ParallelCoords
          bind:this={pcoords}
          fields={dispFields}
          values={dispValues}
          colorValues={dispColorValues}
          colorCategorical={dispCategorical}
          colorMin={dispColorMin}
          colorMax={dispColorMax}
          overlay={pcOverlay}
          overlayColor={pcOverlayColor}
          {selected}
          onbrush={(rows, cons) => { selected = rows; brushConstraints = cons; }}
        />
      </div>
    </section>
  {/if}
</div>

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
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 0 18px;
  }
  .view-seg { flex: none; }
  .view-seg button { padding: 4px 16px; font-size: 12px; }
  .star-box {
    display: flex; justify-content: center; padding: 4px 0;
    border: 1px dashed rgba(45, 212, 191, 0.25); border-radius: 12px;
  }
  h1 { font-size: 18px; margin: 0; font-weight: 600; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
  .topbar-hint { color: var(--muted); font-size: 12px; text-align: right; }

  .graph-region {
    position: absolute; z-index: 1;
    top: 80px; left: 252px; right: 16px; bottom: 232px;
  }
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
  .card-cost { color: var(--muted); font-size: 11px; }
  .x { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 14px; padding: 0 2px; }
  .x:hover { color: #f85149; }
  .card-body { display: flex; align-items: center; gap: 8px; margin-top: 4px; }
  .card-actions { display: flex; flex-direction: column; gap: 6px; }
  .mini {
    font-size: 11px; padding: 3px 8px; border-radius: 6px;
  }
  .morph { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid #2a3441; padding-top: 8px; }
  .morph-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  .morph input[type="range"] { width: 100%; accent-color: var(--accent); }

  .pc-panel {
    left: 252px; right: 16px; bottom: 16px; height: 200px;
    display: flex; flex-direction: column; gap: 6px;
    padding: 12px 16px;
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
  .swatch { display: flex; align-items: center; gap: 6px; }
  .swatch span { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
  .ramp { height: 12px; border-radius: 3px; background: linear-gradient(90deg, #45337a, #3a51a8, #228c8c, #5fbf61, #fce824); }
  .ramp-labels { display: flex; justify-content: space-between; color: var(--muted); }
</style>
