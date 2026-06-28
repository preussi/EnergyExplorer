<script lang="ts">
  import { onMount } from "svelte";
  import { scaleLinear } from "d3-scale";
  import { getShadowPairs, getShadow, type Shadow, type ShadowPair, type ConstraintInput,
           type Dependence, type DependenceMetric } from "./api";
  import { colorFor } from "./colors";

  type RankMetric = "boxiness" | DependenceMetric;

  // Facet views: the exact 2-D shadow (orthogonal projection) of the near-optimal
  // polytope per axis pair — boundaries computed by LPs on the backend, samples
  // drawn inside for density. The MGA-standard way to read trade-off facets.
  let {
    fields = [],
    values = [],
    colorValues = [],
    colorCategorical = false,
    colorMin = 0,
    colorMax = 1,
    constraints = [],
    selected = null,
    dependence = null,
    selectPair = null,
    onconsumepair,
  }: {
    fields: string[];
    values: number[][]; // physical, all axes (sample rows)
    colorValues?: number[];
    colorCategorical?: boolean;
    colorMin?: number;
    colorMax?: number;
    constraints?: ConstraintInput[];
    selected?: number[] | null;
    dependence?: Dependence | null;     // dCor/MI/Pearson, for alternative rankings
    selectPair?: { x: string; y: string } | null;  // one-shot drill-down from the heatmap
    onconsumepair?: () => void;         // ask the parent to clear selectPair once applied
  } = $props();

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;
  const fmt = (v: number) =>
    Math.abs(v) >= 10000 ? v.toExponential(1)
    : Math.abs(v) >= 100 ? v.toFixed(0)
    : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);

  let pairs = $state<ShadowPair[]>([]);
  let pairsLoading = $state(true);
  let sel = $state<{ x: string; y: string } | null>(null);
  let rankMetric = $state<RankMetric>("boxiness");

  // Re-rank the 45 pairs: boxiness ascending (most structured first) or, when a
  // dependence matrix is available, by |dCor|/|MI|/|Pearson| descending (strongest
  // coupling first). Same geometric small-multiples, different ordering lens.
  const rankedPairs = $derived.by(() => {
    const ps = pairs.slice();
    if (rankMetric === "boxiness" || !dependence)
      return ps.sort((a, b) => (a.boxiness ?? 1) - (b.boxiness ?? 1));
    const m = rankMetric;
    const val = (p: ShadowPair) => {
      const i = dependence!.axes.indexOf(p.x), j = dependence!.axes.indexOf(p.y);
      return i >= 0 && j >= 0 ? Math.abs(dependence![m][i][j]) : 0;
    };
    return ps.sort((a, b) => val(b) - val(a));
  });
  function scoreOf(p: ShadowPair): string {
    if (rankMetric === "boxiness" || !dependence)
      return p.boxiness == null ? "" : `${Math.round((1 - p.boxiness) * 100)}%`;
    const i = dependence.axes.indexOf(p.x), j = dependence.axes.indexOf(p.y);
    return i >= 0 && j >= 0 ? dependence[rankMetric][i][j].toFixed(2) : "";
  }

  // drill-down: a pair picked in the Coupling heatmap selects it here. selectPair
  // is a one-shot command — apply it, then ask the parent to clear it. Critically,
  // this effect depends only on selectPair (it never reads `sel`), so clicking
  // other facet cards afterwards is NOT overridden back to the drilled pair.
  $effect(() => {
    const p = selectPair;
    if (p) { sel = { x: p.x, y: p.y }; onconsumepair?.(); }
  });
  let baseShadow = $state<Shadow | null>(null);
  let consShadow = $state<Shadow | null>(null);
  let err = $state<string | null>(null);

  let plotEl: HTMLDivElement;
  let ptsCanvas: HTMLCanvasElement;
  let W = $state(0), H = $state(0);

  const M = { top: 14, right: 16, bottom: 34, left: 58 };

  onMount(() => {
    getShadowPairs()
      .then((r) => {
        pairs = r.pairs;
        pairsLoading = false;
        if (!sel && pairs.length) sel = { x: pairs[0].x, y: pairs[0].y };
      })
      .catch((e) => { err = (e as Error).message; pairsLoading = false; });
    const ro = new ResizeObserver(() => {
      if (!plotEl) return;
      W = plotEl.clientWidth;
      H = plotEl.clientHeight;
    });
    ro.observe(plotEl);
    return () => ro.disconnect();
  });

  // load the base (cached) shadow for the selected pair. Responses carry their
  // pair identity (r.x/r.y) — only accept the one matching the live selection,
  // so rapid pair-clicks with out-of-order responses can't desync the plot.
  $effect(() => {
    const s = sel;
    if (!s) return;
    getShadow(s.x, s.y, [])
      .then((r) => { if (sel && r.x === sel.x && r.y === sel.y) baseShadow = r; })
      .catch((e) => (err = (e as Error).message));
  });

  // load the constrained shadow (debounced; brushing fires rapidly). The old
  // constrained polygon is cleared immediately on any pair/constraint change —
  // it must never be drawn against another pair's scales — and a sequence token
  // discards stale responses (clearTimeout can't cancel an in-flight fetch).
  let consTimer: ReturnType<typeof setTimeout> | null = null;
  let consSeq = 0;
  $effect(() => {
    const s = sel;
    const cons = constraints;
    const my = ++consSeq;
    consShadow = null;
    if (!s || !cons.length) return;
    consTimer = setTimeout(() => {
      getShadow(s.x, s.y, cons)
        .then((r) => { if (my === consSeq && r.x === s.x && r.y === s.y) consShadow = r; })
        .catch(() => { if (my === consSeq) consShadow = null; });
    }, 300);
    return () => { if (consTimer) clearTimeout(consTimer); };
  });

  // scales over the base shadow's bounding box (padded)
  const xi = $derived(sel ? fields.indexOf(sel.x) : -1);
  const yi = $derived(sel ? fields.indexOf(sel.y) : -1);
  const domain = $derived.by(() => {
    if (!baseShadow?.polygon.length) return null;
    const xs = baseShadow.polygon.map((p) => p[0]);
    const ys = baseShadow.polygon.map((p) => p[1]);
    const px = (Math.max(...xs) - Math.min(...xs)) * 0.06 || 1;
    const py = (Math.max(...ys) - Math.min(...ys)) * 0.06 || 1;
    return {
      x: [Math.min(...xs) - px, Math.max(...xs) + px] as [number, number],
      y: [Math.min(...ys) - py, Math.max(...ys) + py] as [number, number],
    };
  });
  const sx = $derived(domain ? scaleLinear().domain(domain.x).range([M.left, W - M.right]) : null);
  const sy = $derived(domain ? scaleLinear().domain(domain.y).range([H - M.bottom, M.top]) : null);

  function polyPath(poly: number[][]): string {
    if (!sx || !sy || !poly.length) return "";
    return "M" + poly.map((p) => `${sx!(p[0]).toFixed(1)},${sy!(p[1]).toFixed(1)}`).join("L") + "Z";
  }

  // sample points on a canvas (subsampled), colored like the map
  $effect(() => {
    const c = ptsCanvas, w = W, h = H;
    const _sx = sx, _sy = sy, xj = xi, yj = yi, sel_ = selected;
    if (!c || !w || !h || !_sx || !_sy || xj < 0 || yj < 0 || !values.length) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = w * dpr; c.height = h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const selSet = sel_ && sel_.length ? new Set(sel_) : null;
    const step = Math.max(1, Math.floor(values.length / 7000));
    for (let r = 0; r < values.length; r += step) {
      const x = _sx(values[r][xj]), y = _sy(values[r][yj]);
      const isSel = !selSet || selSet.has(r);
      ctx.globalAlpha = isSel ? 0.5 : 0.06;
      ctx.fillStyle = colorValues.length
        ? colorFor(colorValues[r], colorMin, colorMax, colorCategorical)
        : "#8b949e";
      ctx.fillRect(x - 1.1, y - 1.1, 2.2, 2.2);
    }
    ctx.globalAlpha = 1;
  });

  // mini polygon paths for the small-multiple cards (own local scales, 84x64 box).
  // `requested` is deliberately non-reactive: the effect must not depend on
  // miniPaths, or every arriving response would re-trigger it and re-fetch
  // everything still in flight.
  let miniPaths = $state<Record<string, string>>({});
  const requested = new Set<string>();
  $effect(() => {
    for (const p of rankedPairs.slice(0, 10)) {
      const key = `${p.x}|${p.y}`;
      if (requested.has(key)) continue;
      requested.add(key);
      getShadow(p.x, p.y, []).then((s) => {
        if (!s.polygon.length) return;
        const xs = s.polygon.map((q) => q[0]), ys = s.polygon.map((q) => q[1]);
        const mx = scaleLinear().domain([Math.min(...xs), Math.max(...xs)]).range([4, 80]);
        const my = scaleLinear().domain([Math.min(...ys), Math.max(...ys)]).range([60, 4]);
        const d = "M" + s.polygon.map((q) => `${mx(q[0]).toFixed(1)},${my(q[1]).toFixed(1)}`).join("L") + "Z";
        miniPaths = { ...miniPaths, [key]: d };
      }).catch(() => { requested.delete(key); });
    }
  });

</script>

<div class="facets">
  <div class="main">
    <div class="plot" bind:this={plotEl}>
      <canvas bind:this={ptsCanvas}></canvas>
      {#if sx && sy && baseShadow}
        <svg>
          <!-- base shadow boundary -->
          <path d={polyPath(baseShadow.polygon)} class="base-poly" />
          <!-- constrained shadow -->
          {#if consShadow}
            {#if consShadow.feasible && consShadow.polygon.length}
              <path d={polyPath(consShadow.polygon)} class="cons-poly" />
            {/if}
          {/if}
          <!-- optimum -->
          <circle cx={sx(baseShadow.optimum[0])} cy={sy(baseShadow.optimum[1])} r="5.5" class="opt" />
          <!-- axes ticks (min/max) -->
          {#if domain}
            <text x={M.left} y={H - M.bottom + 16} class="tick">{fmt(domain.x[0])}</text>
            <text x={W - M.right} y={H - M.bottom + 16} class="tick end">{fmt(domain.x[1])}</text>
            <text x={M.left - 6} y={H - M.bottom} class="tick end">{fmt(domain.y[0])}</text>
            <text x={M.left - 6} y={M.top + 8} class="tick end">{fmt(domain.y[1])}</text>
          {/if}
        </svg>
        <div class="axis-x">{short(baseShadow.x)} →</div>
        <div class="axis-y">{short(baseShadow.y)} →</div>
        {#if consShadow && !consShadow.feasible}
          <div class="empty-note">⚠ constraints make this region empty</div>
        {/if}
      {:else if err}
        <div class="empty-note">⚠ {err}</div>
      {/if}
    </div>
    <p class="caption muted">
      solid outline = exact boundary of the near-optimal space (LP shadow) · teal = under your constraints · ◯ = optimum
    </p>
  </div>

  <div class="rank">
    <div class="rank-head">
      <span>rank by</span>
      <select bind:value={rankMetric} aria-label="ranking metric">
        <option value="boxiness">boxiness</option>
        <option value="dcor" disabled={!dependence}>dist. corr</option>
        <option value="mi" disabled={!dependence}>mutual info</option>
        <option value="pearson" disabled={!dependence}>Pearson</option>
      </select>
    </div>
    {#if pairsLoading}
      <span class="muted small">ranking 45 facets… (first run computes 3,240 LPs)</span>
    {/if}
    {#each rankedPairs.slice(0, 10) as p}
      <button
        class="mini-card"
        class:active={sel?.x === p.x && sel?.y === p.y}
        onclick={() => (sel = { x: p.x, y: p.y })}
      >
        <svg viewBox="0 0 84 64">
          {#if miniPaths[`${p.x}|${p.y}`]}
            <path d={miniPaths[`${p.x}|${p.y}`]} class="mini-poly" />
          {/if}
        </svg>
        <span class="mini-label">{short(p.x)} × {short(p.y)}</span>
        <span class="mini-score" title={rankMetric === "boxiness" ? "how far from a plain rectangle" : rankMetric}>{scoreOf(p)}</span>
      </button>
    {/each}
  </div>
</div>

<style>
  .facets { display: grid; grid-template-columns: 1fr 150px; gap: 12px; width: 100%; height: 100%; min-height: 0; }
  .main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .plot { position: relative; flex: 1; min-height: 0; }
  .plot canvas, .plot svg { position: absolute; inset: 0; width: 100%; height: 100%; }
  .plot svg { overflow: visible; pointer-events: none; }

  .base-poly {
    fill: rgba(255, 255, 255, 0.025);
    stroke: rgba(255, 255, 255, 0.75); stroke-width: 1.6;
    filter: drop-shadow(0 0 5px rgba(255, 255, 255, 0.25));
  }
  .cons-poly {
    fill: rgba(45, 212, 191, 0.10);
    stroke: var(--accent); stroke-width: 1.8;
    filter: drop-shadow(0 0 6px rgba(45, 212, 191, 0.5));
  }
  .opt { fill: #fff; stroke: rgba(0, 0, 0, 0.7); stroke-width: 1.5; }
  .tick { fill: var(--muted); font-size: 10px; }
  .tick.end { text-anchor: end; }
  .axis-x {
    position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
    font-size: 12px; color: var(--accent);
  }
  .axis-y {
    position: absolute; left: 6px; top: 50%;
    transform: translateY(-50%) rotate(180deg); writing-mode: vertical-rl;
    font-size: 12px; color: var(--accent);
  }
  .empty-note {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    color: #f0a14e; font-size: 12px; background: rgba(10, 14, 20, 0.8);
    border-radius: 8px; padding: 4px 10px;
  }
  .caption { font-size: 11px; margin: 6px 0 0; text-align: center; }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }

  .rank { display: flex; flex-direction: column; gap: 7px; overflow-y: auto; min-height: 0; padding-right: 2px; }
  .rank-head { font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
    display: flex; align-items: center; gap: 6px; }
  .rank-head select {
    text-transform: none; letter-spacing: normal; font-size: 11px; padding: 1px 4px;
    background: rgba(255, 255, 255, 0.05); color: var(--fg);
    border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 5px;
  }
  .mini-card {
    display: flex; flex-direction: column; align-items: stretch; gap: 2px;
    background: rgba(255, 255, 255, 0.025);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 9px; padding: 5px 7px; cursor: pointer; text-align: left;
  }
  .mini-card:hover { background: rgba(255, 255, 255, 0.06); }
  .mini-card.active { border-color: var(--accent); box-shadow: 0 0 8px rgba(45, 212, 191, 0.25); }
  .mini-card svg { width: 100%; height: 44px; }
  .mini-poly { fill: rgba(45, 212, 191, 0.12); stroke: var(--accent); stroke-width: 1.4; }
  .mini-label { font-size: 10.5px; color: var(--fg); }
  .mini-score { font-size: 10px; color: var(--muted); }
</style>
