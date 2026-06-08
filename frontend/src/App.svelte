<script lang="ts">
  import {
    getMeta, getProjection, getColor, getSamples, getClusters, generate,
    type Meta, type Projection, type ColorData, type SamplesData, type ClustersData,
    type GenerateResult, type ConstraintInput,
  } from "./lib/api";
  import ScatterGL from "./lib/ScatterGL.svelte";
  import ParallelCoords from "./lib/ParallelCoords.svelte";

  let meta = $state<Meta | null>(null);
  let proj = $state<Projection | null>(null);
  let colorData = $state<ColorData | null>(null);
  let samples = $state<SamplesData | null>(null);
  let clustersData = $state<ClustersData | null>(null);

  let method = $state("pca");
  let sampler = $state("chrrt");
  let field = $state("net_present_cost");
  let space = $state("phys");
  let showOptimum = $state(true);
  let showClusters = $state(true);

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

  // ---- Phase 3: steering / generation ----
  let candidates = $state<GenerateResult | null>(null); // generated set being viewed
  let generating = $state(false);
  let genMsg = $state<string | null>(null);
  let manualConstraints = $state<ConstraintInput[]>([]);
  let brushConstraints = $state<{ axis: string; min: number; max: number }[]>([]);
  let genN = $state(2000);
  // new-constraint form
  let newAxis = $state("nuclear");
  let newMin = $state<number | null>(null);
  let newMax = $state<number | null>(null);

  const allConstraints = $derived<ConstraintInput[]>([...manualConstraints, ...brushConstraints]);

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
      // candidates are PCA-projected; make sure the base view is PCA too
      if (method !== "pca") { method = "pca"; await loadProjection(); loadClusters(); }
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

  function clearSelection() {
    selected = null;
    pcoords?.clearBrushes();
  }

  const PALETTE = ["#e64a3b", "#2e8cdc", "#2ebd78", "#f2c52e", "#9c59b6", "#e67d21"];
  // compact axis names for captions/tooltip
  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;

  async function loadProjection() {
    loading = true; error = null;
    try { proj = await getProjection(method, sampler, 2); }
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
    try { clustersData = await getClusters(method, sampler, 6); }
    catch { clustersData = null; } // e.g. 425 if a projection isn't cached
  }

  function onSamplerOrMethod() {
    selected = null; // rows differ per sampler; method change keeps row ids but reset for clarity
    candidates = null; genMsg = null; // generated set is tied to the previous context
    loadProjection(); loadColor(); loadSamples(); loadClusters();
  }

  // Build short cluster labels from the distinguishing technologies.
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

  $effect(() => {
    getMeta().then((m) => {
      meta = m; loadProjection(); loadColor(); loadSamples(); loadClusters();
    }).catch((e) => (error = (e as Error).message));
  });

  // ---- axis captions (#3) ----
  function topTechs(comp: number[], names: string[], k = 2): string {
    return comp
      .map((w, i) => ({ w, n: names[i] }))
      .sort((a, b) => Math.abs(b.w) - Math.abs(a.w))
      .slice(0, k)
      .map(({ w, n }) => `${w >= 0 ? "↑" : "↓"}${short(n)}`)
      .join(" ");
  }
  function axisLabel(axis: 0 | 1): string {
    if (!proj) return "";
    if (proj.method === "pca" && proj.components && proj.explained_variance) {
      const pct = (proj.explained_variance[axis] * 100).toFixed(1);
      return `PC${axis + 1} · ${pct}% var · ${topTechs(proj.components[axis], proj.feature_names)}`;
    }
    const name = proj.method.toUpperCase();
    return `${name} ${axis + 1}`;
  }
  const isMetric = $derived(proj?.method === "pca");

  // ---- what the scatter & parallel-coords currently display ----
  // (the generated candidate set, or the full sampled space)
  const inCandidates = $derived(!!candidates);
  const dispPoints = $derived(candidates ? candidates.points : (proj?.points ?? []));
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

  // ---- hover tooltip (#2) ----
  const hoverRow = $derived(
    hoverIdx === null ? null
    : candidates ? hoverIdx                      // candidates are row-indexed directly
    : proj ? proj.index[hoverIdx] : null,
  );
  function onMove(e: MouseEvent) {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    mouse = [e.clientX - r.left, e.clientY - r.top];
  }
  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(3);
</script>

<div class="stage" role="presentation" onmousemove={onMove}>
  <!-- top bar (same glass as the side panels) -->
  <header class="hud panel topbar">
    <h1>Energy Explorer <small>near-optimal energy-system designs</small></h1>
    {#if proj}
      <span class="topbar-hint">
        {mode === "select" ? "drag to lasso-select" : "scroll to zoom · drag to pan"} · hover a point for details
        {#if !isMetric} · {proj.method.toUpperCase()} axes are non-metric{/if}
      </span>
    {/if}
  </header>

  <!-- borderless graph filling the space between the top / left / bottom panels -->
  {#if proj && colorData}
    <div class="graph-region">
      <ScatterGL
        bind:this={scatter}
        points={dispPoints}
        color={dispColorValues}
        categorical={dispCategorical}
        optimum={proj.optimum}
        {showOptimum}
        clusters={inCandidates ? [] : clusterMarkers}
        showClusters={showClusters && !inCandidates}
        {selected}
        {mode}
        onhover={(i) => (hoverIdx = i)}
        onselect={(idx) => (selected = idx.length ? idx : null)}
      />
      <div class="ylabel">{axisLabel(1)}</div>
      <div class="xlabel">{axisLabel(0)}</div>
    </div>
  {/if}

  <!-- generated-candidates banner -->
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

  <!-- tooltip lives at the top of the stage so it floats above every panel -->
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
      </select>
    </label>
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
      <input type="checkbox" bind:checked={showOptimum} /> show cost optimum ◯
    </label>
    <label class="check">
      <input type="checkbox" bind:checked={showClusters} /> show cluster labels
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

    <!-- Phase 3: steering / generation -->
    <div class="gen">
      <span class="mode-label">Generate designs</span>

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
          {selected}
          onbrush={(rows, cons) => { selected = rows; brushConstraints = cons; }}
        />
      </div>
    </section>
  {/if}
</div>

<style>
  .stage { position: relative; flex: 1; min-height: 0; overflow: hidden; }

  /* floating heads-up panels */
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
  h1 { font-size: 18px; margin: 0; font-weight: 600; }
  h1 small { color: var(--muted); font-weight: 400; font-size: 13px; margin-left: 8px; }
  .topbar-hint { color: var(--muted); font-size: 12px; text-align: right; }

  /* borderless graph between the panels */
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
    display: flex; flex-direction: column; gap: 14px; overflow-y: auto;
    padding: 16px;
  }
  .check { flex-direction: row; align-items: center; gap: 6px; color: var(--fg); }
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

  /* generate / constraints (Phase 3) */
  .gen { display: flex; flex-direction: column; gap: 8px; border-top: 1px solid #2a3441; padding-top: 12px; }
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

  /* bottom floating parallel-coordinates panel (right of the sidebar) */
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
    position: absolute; pointer-events: none; z-index: 50; /* above all panels */
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
