<script lang="ts">
  import { onMount } from "svelte";
  import { scaleLinear, type ScaleLinear } from "d3-scale";
  import { colorFor } from "./colors";

  let {
    fields = [],
    values = [],
    selected = null,
    colorValues = [],
    colorCategorical = false,
    colorMin = 0,
    colorMax = 1,
    overlay = null,
    overlayColor = "#ffffff",
    staticLines = [],
    domainExtra = [],
    showViolins = false,
    flexRanges = [],
    onbrush,
  }: {
    fields: string[];
    values: number[][];
    selected?: number[] | null;
    colorValues?: number[];
    colorCategorical?: boolean;
    colorMin?: number;
    colorMax?: number;
    overlay?: number[] | null; // one design's values (all fields) drawn as a bright line
    overlayColor?: string;
    // designs drawn statically in their own colors (e.g. the A/B interpolation
    // anchors), under the moving `overlay` line.
    staticLines?: { values: number[]; color: string }[];
    // extra design rows the axes must contain (e.g. pinned extreme designs that
    // lie outside the sampled range) so their lines stay within the plot.
    domainExtra?: number[][];
    // when true, draw a marginal-density violin per axis instead of the 20k faint
    // polylines; individual lines are then drawn only for the active subset.
    showViolins?: boolean;
    // exact LP feasible [min,max] per axis under current constraints, drawn as a
    // band on each axis (physical units, matched to fields by `axis` name).
    flexRanges?: { axis: string; min: number | null; max: number | null }[];
    onbrush?: (
      rows: number[] | null,
      constraints: { axis: string; min: number; max: number }[],
    ) => void;
  } = $props();

  // Per-row line color, matching the scatter's "Color by" encoding.
  const rowColor = $derived(
    colorValues.length
      ? colorValues.map((v) => colorFor(v, colorMin, colorMax, colorCategorical))
      : [],
  );

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;
  const M = { top: 34, right: 30, bottom: 28, left: 30 };

  let container: HTMLDivElement;
  let base: HTMLCanvasElement; // all designs, faint (drawn once per layout)
  let hl: HTMLCanvasElement; // selected designs, bright

  // Reactive state consumed by the SVG overlay.
  let W = $state(0), H = $state(0);
  let scales = $state<ScaleLinear<number, number>[]>([]);
  let xPos = $state<number[]>([]);
  let brushes = $state<([number, number] | null)[]>([]);

  type Layout = { sc: ScaleLinear<number, number>[]; xp: number[] };

  function buildLayout(w: number, h: number): Layout {
    const n = fields.length;
    const xp = fields.map((_, i) =>
      n === 1 ? w / 2 : M.left + (i * (w - M.left - M.right)) / (n - 1),
    );
    const sc = fields.map((_, a) => {
      let lo = Infinity, hi = -Infinity;
      for (let r = 0; r < values.length; r++) {
        const v = values[r][a];
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      // widen the axis to contain any out-of-sample designs (e.g. pinned extremes)
      for (const row of domainExtra) {
        const v = row?.[a];
        if (v == null) continue;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }
      // and to contain the exact LP feasible range (its bounds sit at the polytope
      // boundary, past where the samples reach), so the flex band fits on-axis
      const rg = flexRanges.find((r) => r.axis === fields[a]);
      if (rg) {
        if (rg.min != null && rg.min < lo) lo = rg.min;
        if (rg.max != null && rg.max > hi) hi = rg.max;
      }
      if (lo === hi) hi = lo + 1;
      return scaleLinear().domain([lo, hi]).range([h - M.bottom, M.top]);
    });
    return { sc, xp };
  }

  function setupCanvas(c: HTMLCanvasElement, w: number, h: number) {
    const dpr = window.devicePixelRatio || 1;
    c.width = w * dpr;
    c.height = h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  function strokeRows(
    ctx: CanvasRenderingContext2D, rows: Iterable<number>,
    sc: ScaleLinear<number, number>[], xp: number[],
  ) {
    ctx.beginPath();
    for (const r of rows) {
      const row = values[r];
      for (let a = 0; a < xp.length; a++) {
        const x = xp[a], y = sc[a](row[a]);
        if (a === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
    }
    ctx.stroke();
  }

  // Draw lines colored by the current field. Rows are grouped by color so the
  // whole set renders in ~64 stroke calls instead of one per line.
  function strokeColored(
    ctx: CanvasRenderingContext2D, rows: Iterable<number>, alpha: number,
    sc: ScaleLinear<number, number>[], xp: number[], rc: string[],
  ) {
    const groups = new Map<string, number[]>();
    for (const r of rows) {
      const c = rc[r] ?? "#8b949e";
      const g = groups.get(c);
      if (g) g.push(r);
      else groups.set(c, [r]);
    }
    ctx.globalAlpha = alpha;
    for (const [c, rs] of groups) {
      ctx.strokeStyle = c;
      strokeRows(ctx, rs, sc, xp);
    }
    ctx.globalAlpha = 1;
  }

  function measure() {
    if (!container) return;
    W = container.clientWidth;
    H = container.clientHeight;
  }

  // ---- brushing ----
  let drag: { axis: number; startY: number; moved: boolean } | null = null;
  const clampY = (y: number) => Math.max(M.top, Math.min(H - M.bottom, y));
  const localY = (e: PointerEvent) => e.clientY - container.getBoundingClientRect().top;

  function nearestAxis(clientX: number) {
    const x = clientX - container.getBoundingClientRect().left;
    let best = 0, bd = Infinity;
    xPos.forEach((px, i) => { const d = Math.abs(px - x); if (d < bd) { bd = d; best = i; } });
    return bd <= 20 ? best : -1;
  }
  function onDown(e: PointerEvent) {
    const axis = nearestAxis(e.clientX);
    if (axis < 0) return;
    drag = { axis, startY: localY(e), moved: false };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }
  function onMove(e: PointerEvent) {
    if (!drag) return;
    const y = localY(e);
    if (Math.abs(y - drag.startY) > 2) drag.moved = true;
    const sc = scales[drag.axis];
    const a = sc.invert(clampY(drag.startY));
    const b = sc.invert(clampY(y));
    const next = [...brushes];
    next[drag.axis] = [Math.min(a, b), Math.max(a, b)];
    brushes = next;
    applyBrushes();
  }
  function onUp() {
    if (drag && !drag.moved) {
      const next = [...brushes];
      next[drag.axis] = null; // click clears that axis
      brushes = next;
      applyBrushes();
    }
    drag = null;
  }
  function applyBrushes() {
    const active = brushes.map((b, a) => ({ b, a })).filter((x) => x.b);
    if (!active.length) { onbrush?.(null, []); return; }
    const rows: number[] = [];
    for (let r = 0; r < values.length; r++) {
      let ok = true;
      for (const { b, a } of active) {
        const v = values[r][a];
        if (v < b![0] || v > b![1]) { ok = false; break; }
      }
      if (ok) rows.push(r);
    }
    const cons = active.map(({ b, a }) => ({ axis: fields[a], min: b![0], max: b![1] }));
    onbrush?.(rows, cons);
  }
  export function clearBrushes() {
    brushes = fields.map(() => null);
    onbrush?.(null, []);
  }

  const fmt = (v: number) =>
    Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 0 })
    : Math.abs(v) >= 1 ? v.toFixed(0) : v.toFixed(2);

  onMount(() => {
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(container);
    return () => ro.disconnect();
  });

  // Reset brushes when the dataset changes (deps: fields, values only).
  $effect(() => {
    fields; values;
    brushes = fields.map(() => null);
  });

  // Per-axis marginal-density violin over a set of rows (all designs, or a brushed
  // subset for the conditional distribution). Each axis is self-normalized so the
  // shape is visible; `rows === null` means the full population.
  const VBINS = 44;
  function violinHalfMax(xp: number[]): number {
    const spacing = fields.length > 1 ? Math.abs(xp[1] - xp[0]) : W - M.left - M.right;
    return Math.min(26, spacing * 0.4);
  }
  function drawViolins(
    ctx: CanvasRenderingContext2D,
    sc: ScaleLinear<number, number>[], xp: number[],
    rows: number[] | null, fill: string, stroke: string,
  ) {
    const halfMax = violinHalfMax(xp);
    const N = rows ? rows.length : values.length;
    if (!N) return;
    for (let a = 0; a < fields.length; a++) {
      const scale = sc[a];
      const [dlo, dhi] = scale.domain();
      const span = dhi - dlo || 1;
      const counts = new Array(VBINS).fill(0);
      const add = (v: number) => {
        let bin = Math.floor(((v - dlo) / span) * VBINS);
        if (bin < 0) bin = 0; else if (bin >= VBINS) bin = VBINS - 1;
        counts[bin]++;
      };
      if (rows) for (const r of rows) add(values[r][a]);
      else for (let r = 0; r < values.length; r++) add(values[r][a]);
      // 3-tap smoothing for a violin (rather than blocky histogram) look
      const sm = counts.map((c, i) =>
        ((counts[i - 1] ?? c) + 2 * c + (counts[i + 1] ?? c)) / 4);
      const maxC = Math.max(...sm) || 1;
      const x = xp[a];
      const yOf = (i: number) => scale(dlo + ((i + 0.5) / VBINS) * span);
      ctx.beginPath();
      for (let i = 0; i < VBINS; i++) {
        const hw = (sm[i] / maxC) * halfMax;
        if (i === 0) ctx.moveTo(x + hw, yOf(i));
        else ctx.lineTo(x + hw, yOf(i));
      }
      for (let i = VBINS - 1; i >= 0; i--) ctx.lineTo(x - (sm[i] / maxC) * halfMax, yOf(i));
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  // Exact LP feasible [min,max] per axis, drawn as a band behind the violin. The
  // band typically extends past the sample density (samples thin out at the
  // polytope boundary), which is exactly the point — provable range vs density.
  function drawFlexBands(
    ctx: CanvasRenderingContext2D,
    sc: ScaleLinear<number, number>[], xp: number[],
  ) {
    if (!flexRanges.length) return;
    const half = violinHalfMax(xp) + 5;
    for (let a = 0; a < fields.length; a++) {
      const rg = flexRanges.find((r) => r.axis === fields[a]);
      if (!rg || rg.min == null || rg.max == null) continue;
      const yHi = sc[a](rg.max), yLo = sc[a](rg.min);
      const x = xp[a];
      ctx.fillStyle = "rgba(244,180,60,0.10)";
      ctx.fillRect(x - half, yHi, half * 2, yLo - yHi);
      ctx.strokeStyle = "rgba(244,180,60,0.7)";
      ctx.lineWidth = 1.2;
      for (const y of [yHi, yLo]) {
        ctx.beginPath();
        ctx.moveTo(x - half, y);
        ctx.lineTo(x + half, y);
        ctx.stroke();
      }
    }
  }

  // Layout + base layer. Writes scales/xPos (for the SVG) but never reads them,
  // so it cannot re-trigger itself. Re-runs on data or size change.
  $effect(() => {
    const f = fields, v = values, w = W, h = H, de = domainExtra, vi = showViolins, fr = flexRanges;
    if (!base || !w || !h || !f.length || !v.length || !de) return;
    const { sc, xp } = buildLayout(w, h);
    scales = sc;
    xPos = xp;
    const ctx = setupCanvas(base, w, h);
    ctx.clearRect(0, 0, w, h);
    if (vi) {
      // exact-range bands behind, then the full-population violin (neutral ghost);
      // the brushed conditional violin is drawn brighter on the highlight layer.
      if (fr.length) drawFlexBands(ctx, sc, xp);
      drawViolins(ctx, sc, xp, null, "rgba(139,148,158,0.13)", "rgba(139,148,158,0.4)");
    } else {
      // faint neutral context for ALL designs (so a selection reads as "the rest")
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = "rgba(139,148,158,0.06)";
      strokeRows(ctx, v.keys(), sc, xp);
    }
  });

  // Highlight layer, colored by the current field. When a selection is active we
  // color just the selection (over the faint base); otherwise we color all rows
  // as a vivid overview. An optional `overlay` design (e.g. the A→B morph or a
  // hovered pin) is drawn on top as a single bright glowing line.
  // Reads scales/xPos/selected/rowColor/overlay; writes nothing.
  $effect(() => {
    const sel = selected, w = W, h = H, sc = scales, xp = xPos, rc = rowColor;
    const ov = overlay, oc = overlayColor, stat = staticLines, vi = showViolins;
    if (!hl || !w || !h || !sc.length) return;
    const ctx = setupCanvas(hl, w, h);
    ctx.clearRect(0, 0, w, h);
    const hasSel = !!(sel && sel.length);
    if (vi) {
      // conditional distribution: the brushed subset as a bright violin on top of
      // the ghost full-population one (answers "if I fix this, what happens?").
      if (hasSel) drawViolins(ctx, sc, xp, sel!, "rgba(45,212,191,0.30)", "rgba(45,212,191,0.9)");
    } else {
      // non-violin mode: color the selection, or the whole set as a vivid overview.
      const rows: Iterable<number> = hasSel ? sel! : values.keys();
      ctx.lineWidth = hasSel ? 0.9 : 0.5;
      strokeColored(ctx, rows, hasSel ? 0.5 : 0.22, sc, xp, rc);
    }

    const drawLine = (vals: number[], color: string, width: number, glow: number, alpha: number) => {
      if (!vals || vals.length !== fields.length) return;
      ctx.lineWidth = width;
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = glow;
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      for (let a = 0; a < xp.length; a++) {
        const x = xp[a], y = sc[a](vals[a]);
        if (a === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.shadowBlur = 0;
      ctx.globalAlpha = 1;
    };

    // static anchor designs (e.g. the A/B interpolation endpoints) in their colors
    for (const s of stat) drawLine(s.values, s.color, 2.0, 4, 0.9);

    // moving overlay design (e.g. the A→B interpolation) on top
    if (ov) drawLine(ov, oc, 2.4, 7, 0.95);
  });
</script>

<div class="pc" bind:this={container}>
  <canvas class="layer" bind:this={base}></canvas>
  <canvas class="layer" bind:this={hl}></canvas>
  <svg
    class="layer"
    onpointerdown={onDown}
    onpointermove={onMove}
    onpointerup={onUp}
    role="presentation"
  >
    {#each fields as f, a}
      {#if scales[a] !== undefined && xPos[a] !== undefined}
        <line class="axis" x1={xPos[a]} y1={M.top} x2={xPos[a]} y2={H - M.bottom} />
        <text class="axis-name" x={xPos[a]} y={M.top - 18}>{short(f)}</text>
        <text class="tick" x={xPos[a]} y={M.top - 5}>{fmt(scales[a].domain()[1])}</text>
        <text class="tick bot" x={xPos[a]} y={H - M.bottom + 12}>{fmt(scales[a].domain()[0])}</text>
        {#if brushes[a]}
          <rect
            class="brush"
            x={xPos[a] - 8}
            y={scales[a](brushes[a]![1])}
            width="16"
            height={Math.max(2, scales[a](brushes[a]![0]) - scales[a](brushes[a]![1]))}
          />
        {/if}
      {/if}
    {/each}
  </svg>
</div>

<style>
  .pc { position: relative; width: 100%; height: 100%; min-height: 0; }
  .layer { position: absolute; inset: 0; width: 100%; height: 100%; }
  svg { overflow: visible; cursor: crosshair; }
  .axis { stroke: #45505c; stroke-width: 1; }
  .axis-name { fill: var(--accent); font-size: 11px; text-anchor: middle; font-weight: 600; }
  .tick { fill: var(--muted); font-size: 9px; text-anchor: middle; }
  .tick.bot { dominant-baseline: hanging; }
  .brush { fill: rgba(45, 212, 191, 0.18); stroke: var(--accent); stroke-width: 1; pointer-events: none; }
</style>
