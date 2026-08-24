<script lang="ts">
  import { onMount } from "svelte";
  import { scaleLinear } from "d3-scale";
  import { getShadow, type Shadow, type ShadowPair, type ConstraintInput } from "./api";
  import { colorFor } from "./colors";

  // Facet view: the exact 2-D shadow (orthogonal projection) of the near-optimal
  // polytope for one axis pair — boundary computed by LPs on the backend, samples
  // drawn inside for density. The MGA-standard way to read trade-off facets.
  // Which pair is shown is the caller's business: the Coupling matrix drives it
  // through `selectPair`, so there is no picker or ranked list in here.
  let {
    fields = [],
    values = [],
    activeFields = [],
    colorValues = [],
    colorCategorical = false,
    theme = "dark",
    colorMin = 0,
    colorMax = 1,
    constraints = [],
    selected = null,
    selectPair = null,
    shadowPairs = [],
    onconsumepair,
  }: {
    fields: string[];
    values: number[][]; // physical, all axes (sample rows)
    activeFields?: string[];            // dimensions enabled in Settings ([] = all)
    colorValues?: number[];
    colorCategorical?: boolean;
    /** selects the mark ramp and forces a canvas repaint */
    theme?: "dark" | "light";
    colorMin?: number;
    colorMax?: number;
    constraints?: ConstraintInput[];
    selected?: number[] | null;
    selectPair?: { x: string; y: string } | null;  // one-shot drill-down from the heatmap
    /** boxiness-ranked pairs the parent already holds. Only used to fall back to a
        still-valid pair if the drawn one's axes get disabled — we never fetch them
        ourselves (the matrix that drives us is built from the same payload). */
    shadowPairs?: ShadowPair[];
    onconsumepair?: () => void;         // ask the parent to clear selectPair once applied
  } = $props();

  // A pair is selectable only when both of its axes are enabled in Settings.
  const activeSet = $derived(new Set(activeFields));
  const pairActive = (p: { x: string; y: string }) =>
    activeFields.length === 0 || (activeSet.has(p.x) && activeSet.has(p.y));

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;
  const fmt = (v: number) =>
    Math.abs(v) >= 10000 ? v.toExponential(1)
    : Math.abs(v) >= 100 ? v.toFixed(0)
    : Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);

  let sel = $state<{ x: string; y: string } | null>(null);

  // Still-drawable pairs, most structured (lowest boxiness) first — the fallback
  // order when the drawn pair stops being valid.
  const activePairs = $derived(
    shadowPairs.filter(pairActive).sort((a, b) => (a.boxiness ?? 1) - (b.boxiness ?? 1)),
  );

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
    const ro = new ResizeObserver(() => {
      if (!plotEl) return;
      W = plotEl.clientWidth;
      H = plotEl.clientHeight;
    });
    ro.observe(plotEl);
    return () => ro.disconnect();
  });

  // If the selected pair's axes get disabled in Settings (or none is chosen yet),
  // fall back to the top still-active pair so the plot never shows a hidden axis.
  // Converges in one step: once sel is an active pair the condition is false.
  $effect(() => {
    if (!shadowPairs.length) return;
    if ((sel && !pairActive(sel)) || (!sel && activePairs.length)) {
      sel = activePairs.length ? { x: activePairs[0].x, y: activePairs[0].y } : null;
    }
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
  // True feasible extent of the shadow (exact LP min/max of each axis over the
  // polytope) — this is what the violins below use as their axis domain too, so
  // the tick labels must show *these* numbers, not the padded drawing domain.
  const extent = $derived.by(() => {
    if (!baseShadow?.polygon.length) return null;
    const xs = baseShadow.polygon.map((p) => p[0]);
    const ys = baseShadow.polygon.map((p) => p[1]);
    return {
      x: [Math.min(...xs), Math.max(...xs)] as [number, number],
      y: [Math.min(...ys), Math.max(...ys)] as [number, number],
    };
  });
  // Drawing domain: pad 6% so the polygon isn't flush against the axes. Padding
  // never leaks into the labels, and we never pad *below* a true 0 (technology
  // capacities are non-negative — a padded −6.6 would be nonsensical).
  const domain = $derived.by(() => {
    if (!extent) return null;
    const pad = ([lo, hi]: [number, number]): [number, number] => {
      const d = (hi - lo) * 0.06 || 1;
      return [lo - (lo <= 0 ? 0 : d), hi + d];
    };
    return { x: pad(extent.x), y: pad(extent.y) };
  });
  // snap numerical −0 / floating-point dust (e.g. LP min = −1e−12) to a clean 0
  const tickFmt = (v: number, span: number) => fmt(Math.abs(v) < 1e-6 * (span || 1) ? 0 : v);
  // Square plotting box: both axes get the same pixel length, so the shadow's
  // shape isn't stretched by the panel's aspect ratio. Largest square that fits
  // inside the margins, centered in whatever space is left over.
  const geo = $derived.by(() => {
    const innerW = Math.max(0, W - M.left - M.right);
    const innerH = Math.max(0, H - M.top - M.bottom);
    const S = Math.min(innerW, innerH);
    const x0 = M.left + (innerW - S) / 2;
    const y1 = M.top + (innerH - S) / 2;      // top edge
    return { S, x0, x1: x0 + S, y1, y0: y1 + S };
  });
  const sx = $derived(domain && geo.S > 0 ? scaleLinear().domain(domain.x).range([geo.x0, geo.x1]) : null);
  const sy = $derived(domain && geo.S > 0 ? scaleLinear().domain(domain.y).range([geo.y0, geo.y1]) : null);

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
        ? colorFor(colorValues[r], colorMin, colorMax, colorCategorical, theme)
        : "#8b949e";
      ctx.fillRect(x - 1.1, y - 1.1, 2.2, 2.2);
    }
    ctx.globalAlpha = 1;
  });

  let howto = $state(false);
</script>

<div class="facets">
  <div class="main">
    <div class="plot" bind:this={plotEl}>
      <canvas bind:this={ptsCanvas}></canvas>
      {#if sel}
        <button class="swap" onclick={() => sel && (sel = { x: sel.y, y: sel.x })}
                title="swap the x and y axes">⇄ swap axes</button>
      {/if}
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
          <!-- axis ticks: the true feasible min/max (LP extent), placed at the
               polygon edges — matches the violin axes below -->
          {#if extent}
            <text x={sx(extent.x[0])} y={geo.y0 + 16} class="tick">{tickFmt(extent.x[0], extent.x[1] - extent.x[0])}</text>
            <text x={sx(extent.x[1])} y={geo.y0 + 16} class="tick end">{tickFmt(extent.x[1], extent.x[1] - extent.x[0])}</text>
            <text x={geo.x0 - 6} y={sy(extent.y[0])} class="tick end">{tickFmt(extent.y[0], extent.y[1] - extent.y[0])}</text>
            <text x={geo.x0 - 6} y={sy(extent.y[1]) + 8} class="tick end">{tickFmt(extent.y[1], extent.y[1] - extent.y[0])}</text>
          {/if}
          <!-- axis titles, aligned to the square box -->
          <text class="axis-lbl" x={(geo.x0 + geo.x1) / 2} y={geo.y0 + 30} text-anchor="middle">{short(baseShadow.x)} →</text>
          <text class="axis-lbl"
                transform="rotate(-90 {geo.x0 - 42} {(geo.y0 + geo.y1) / 2})"
                x={geo.x0 - 42} y={(geo.y0 + geo.y1) / 2} text-anchor="middle">{short(baseShadow.y)} →</text>
        </svg>
        {#if consShadow && !consShadow.feasible}
          <div class="empty-note">⚠ constraints make this region empty</div>
        {/if}
      {:else if err}
        <div class="empty-note">⚠ {err}</div>
      {/if}
    </div>
    <p class="caption muted">
      <button class="info" class:on={howto} aria-label="how to read this facet"
              title="how to read this" onclick={() => (howto = !howto)}>i</button>
      {#if howto}
        solid outline = exact boundary of the near-optimal space (LP shadow) · dots =
        sampled designs (they rarely reach the extreme corners, which is why the
        violins look narrower) · teal = under your constraints · ◯ = optimum
      {/if}
    </p>
  </div>

</div>

<style>
  .info {
    width: 15px; height: 15px; padding: 0; border-radius: 50%; vertical-align: middle;
    font-size: 9.5px; font-style: italic; font-weight: 700; line-height: 1;
    background: var(--s-05); border: 1px solid var(--b-20); color: var(--muted); cursor: pointer;
  }
  .info.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }
  .facets { display: flex; width: 100%; height: 100%; min-height: 0; }
  .main { display: flex; flex-direction: column; flex: 1; min-width: 0; min-height: 0; }
  .plot { position: relative; flex: 1; min-height: 0; }
  .plot canvas, .plot svg { position: absolute; inset: 0; width: 100%; height: 100%; }
  .plot svg { overflow: visible; pointer-events: none; }

  .swap {
    position: absolute; top: 8px; right: 8px; z-index: 2;
    font-size: 11px; padding: 3px 9px; border-radius: 7px;
    background: var(--s-05); border: 1px solid var(--b-12);
    color: var(--fg); cursor: pointer;
  }
  .swap:hover { background: var(--s-09); border-color: var(--accent); }

  .base-poly {
    fill: var(--s-02);
    stroke: var(--tick); stroke-width: 1.6;
  }
  .cons-poly {
    filter: drop-shadow(0 0 6px color-mix(in srgb, var(--accent) 50%, transparent));
    fill: color-mix(in srgb, var(--accent) 10%, transparent);
    stroke: var(--accent); stroke-width: 1.8;
  }
  .opt {
    filter: drop-shadow(0 0 5px var(--tick)); fill: var(--tick); stroke: var(--halo); stroke-width: 1.5; }
  .tick { fill: var(--muted); font-size: 10px; }
  .tick.end { text-anchor: end; }
  .axis-lbl { fill: var(--accent); font-size: 12px; }
  .empty-note {
    position: absolute; top: 12px; left: 50%; transform: translateX(-50%);
    color: var(--warn); font-size: 12px; background: var(--panel-glass);
    border-radius: 8px; padding: 4px 10px;
  }
  .caption { font-size: 11px; margin: 6px 0 0; text-align: center; }
  .muted { color: var(--muted); }

</style>
