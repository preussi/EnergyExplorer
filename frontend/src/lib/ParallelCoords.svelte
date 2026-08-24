<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { scaleLinear, type ScaleLinear } from "d3-scale";
  import { canvasTokens, colorFor, shortUnit } from "./colors";

  let {
    fields = [],
    values = [],
    selected = null,
    colorValues = [],
    colorCategorical = false,
    colorMin = 0,
    colorMax = 1,
    overlay = null,
    overlayColor = null,
    staticLines = [],
    domainExtra = [],
    showViolins = false,
    flexRanges = [],
    flexBase = [],
    optimum = [],
    units = {},
    showOptimum = true,
    flexFeasible = true,
    flexBusy = false,
    marginalValues = null,
    marginalBusy = false,
    theme = "dark",
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
    /** null => the component picks --tick, i.e. a mark that reads against the
     *  CURRENT surface. It defaulted to a literal "#ffffff", which is invisible
     *  on a light panel. Only pass a colour when it carries meaning (a pin's). */
    overlayColor?: string | null;
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
    // the same ranges with NO constraints applied — the full near-optimal span.
    // Drawn as a grey track behind `flexRanges` so the shrinkage is legible, and
    // used for the "% of range left" readout.
    flexBase?: { axis: string; min: number | null; max: number | null }[];
    // cost-optimal value per axis, ticked on each axis. Keyed by `axis` NAME, not
    // index: our columns are permuted by the caller's coupling seriation and can
    // come from a candidate set, so an index-keyed optimum would land on the
    // wrong axis.
    optimum?: { axis: string; value: number }[];
    // physical unit per axis name (GW / GWh / ktCO2eq per hour). Shown on the row
    // so a bare "12.4" can't be read as the wrong quantity.
    units?: Record<string, string>;
    // draw the optimum tick on each row. Separate from the Map's own `showOptimum`
    // overlay flag — that one is map-only state and the rows are in both views.
    showOptimum?: boolean;
    // false when the current constraints admit no near-optimal design at all
    flexFeasible?: boolean;
    // true while the flexibility LPs are in flight — dims the flex decorations
    flexBusy?: boolean;
    /** Distribution the violins draw, when it is NOT simply `values`.
     *
     *  With every axis shown the two coincide, so this stays null and the base
     *  cloud is used. With a strict subset shown it carries a uniform sample of
     *  the PROJECTION onto those axes (worldview B), one column per entry of
     *  `fields`, in the same order — so hiding an axis genuinely reshapes the
     *  remaining violins instead of leaving them identical.
     *
     *  It drives the ghost violin AND the brushed conditional one, so both come
     *  from the same distribution; `selected` still indexes `values` and keeps
     *  driving the polylines, the count and everything outside this component. */
    marginalValues?: number[][] | null;
    /** true while that sample is being re-drawn from the backend */
    marginalBusy?: boolean;
    /** a canvas holds baked pixels, so it must be told to repaint — CSS alone
     *  cannot restyle it. Also selects the mark ramp. */
    theme?: "dark" | "light";
    onbrush?: (
      rows: number[] | null,
      constraints: { axis: string; min: number; max: number }[],
    ) => void;
  } = $props();

  // Per-row line color, matching the scatter's "Color by" encoding.
  const rowColor = $derived(
    colorValues.length
      ? colorValues.map((v) => colorFor(v, colorMin, colorMax, colorCategorical, theme))
      : [],
  );

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;
  // ROTATED parallel coordinates: each axis is a horizontal ROW, not a vertical
  // column, so the whole plot fits a narrow side rail. A design is still one
  // polyline, but it runs top-to-bottom, its x on each row being its value there.
  // Consequence throughout: scales map value → X, and yPos[a] is the row centre.
  // `left` is the label gutter; `bottom` leaves room for the last row's ticks.
  const M = { top: 10, right: 14, bottom: 14, left: 62 };

  // Axis-name lookups for the flex decorations, so the render loops stop doing a
  // linear `find` per axis per frame.
  type Range = { axis: string; min: number | null; max: number | null };
  const flexBy = $derived(new Map(flexRanges.map((r) => [r.axis, r])));
  const baseBy = $derived(new Map(flexBase.map((r) => [r.axis, r])));
  const optBy = $derived(new Map(optimum.map((o) => [o.axis, o.value])));
  const span = (r: Range | undefined) =>
    r && r.min != null && r.max != null ? r.max - r.min : null;
  // fraction of the unconstrained range still feasible, or null when we can't say
  function pctLeft(f: string): number | null {
    const b = span(baseBy.get(f)), c = span(flexBy.get(f));
    if (b == null || c == null || b <= 0) return null;
    return Math.max(0, Math.min(1, c / b)) * 100;
  }

  let container: HTMLDivElement;
  let base: HTMLCanvasElement; // all designs, faint (drawn once per layout)
  let hl: HTMLCanvasElement; // selected designs, bright

  // Reactive state consumed by the SVG overlay.
  let W = $state(0), H = $state(0);
  let scales = $state<ScaleLinear<number, number>[]>([]);
  let yPos = $state<number[]>([]);   // row centre per axis
  let rowH = $state(0);              // vertical pitch between rows
  let brushes = $state<([number, number] | null)[]>([]);

  type Layout = { sc: ScaleLinear<number, number>[]; yp: number[]; rh: number };

  // half-thickness of a row's violin / flex band, capped so rows never merge and
  // the tick line under each row stays clear of the next one
  // Half-thickness of a row's violin / flex band. The CAP is what decides how full
  // the rail looks: at 13 a 9-axis rail drew ~43 px of ink into a ~76 px pitch, so
  // the panel read as mostly empty space with thin ribbons in it. 22 uses ~61 px of
  // the same pitch and still leaves a clear gutter between rows. The floor of 5 is
  // the point where a violin stops being a shape, and is what sets MAX_AXES.
  const halfOf = (rh: number) => Math.max(5, Math.min(22, rh * 0.34));

  function buildLayout(w: number, h: number): Layout {
    const n = fields.length;
    const rh = (h - M.top - M.bottom) / Math.max(1, n);
    const yp = fields.map((_, i) => M.top + (i + 0.5) * rh);
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
      // boundary, past where the samples reach), so the flex band fits on-axis.
      // `flexBase` (the UNCONSTRAINED range) is unioned in too, which pins the
      // domain: tightening a constraint shrinks the band inside a fixed axis
      // instead of rescaling every axis under you mid-brush.
      for (const rg of [flexBy.get(fields[a]), baseBy.get(fields[a])]) {
        if (!rg) continue;
        if (rg.min != null && rg.min < lo) lo = rg.min;
        if (rg.max != null && rg.max > hi) hi = rg.max;
      }
      if (lo === hi) hi = lo + 1;
      return scaleLinear().domain([lo, hi]).range([M.left, w - M.right]);
    });
    return { sc, yp, rh };
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
    sc: ScaleLinear<number, number>[], yp: number[],
  ) {
    ctx.beginPath();
    for (const r of rows) {
      const row = values[r];
      for (let a = 0; a < yp.length; a++) {
        const x = sc[a](row[a]), y = yp[a];
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
    sc: ScaleLinear<number, number>[], yp: number[], rc: string[],
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
      strokeRows(ctx, rs, sc, yp);
    }
    ctx.globalAlpha = 1;
  }

  function measure() {
    if (!container) return;
    W = container.clientWidth;
    H = container.clientHeight;
  }

  // ---- brushing: drag sideways along a row to keep a value band ----
  let drag: { axis: number; startX: number; moved: boolean } | null = null;
  const clampX = (x: number) => Math.max(M.left, Math.min(W - M.right, x));
  const localX = (e: PointerEvent) => e.clientX - container.getBoundingClientRect().left;

  function nearestAxis(clientY: number) {
    const y = clientY - container.getBoundingClientRect().top;
    let best = 0, bd = Infinity;
    yPos.forEach((py, i) => { const d = Math.abs(py - y); if (d < bd) { bd = d; best = i; } });
    // half the row pitch, so every pixel of the plot belongs to exactly one row
    return bd <= Math.max(12, rowH * 0.5) ? best : -1;
  }
  function onDown(e: PointerEvent) {
    const axis = nearestAxis(e.clientY);
    if (axis < 0) return;
    drag = { axis, startX: localX(e), moved: false };
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }
  function onMove(e: PointerEvent) {
    if (!drag) return;
    const x = localX(e);
    if (Math.abs(x - drag.startX) > 2) drag.moved = true;
    const sc = scales[drag.axis];
    const a = sc.invert(clampX(drag.startX));
    const b = sc.invert(clampX(x));
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
  function applyBrushes(src: ([number, number] | null)[] = brushes) {
    const active = src.map((b, a) => ({ b, a })).filter((x) => x.b);
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
  /** Set one row's brush programmatically (the guided tour drives the demo this
   *  way). Goes through the same `brushes` state as dragging, so the rect shows
   *  up on the row rather than a constraint appearing from nowhere. */
  export function setBrush(axis: string, lo: number, hi: number) {
    const a = fields.indexOf(axis);
    if (a < 0) return;
    const next = fields.map(() => null) as ([number, number] | null)[];
    next[a] = [Math.min(lo, hi), Math.max(lo, hi)];
    brushes = next;
    applyBrushes();
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

  // Keep each brush attached to its AXIS, not to its column position.
  //
  // `fields` changes whenever the visible subset changes (Settings) or the order
  // does (coupled order). The old code blanked the whole array without telling the
  // host, so hiding any axis made every brush rectangle vanish while the
  // constraints stayed live and invisible — still driving flexibility and
  // resampling with nothing on screen to explain them.
  //
  // Hiding an axis DROPS its constraint (the alternative — an active constraint
  // you can't see — is exactly what caused the bug). We notify only when the set
  // of constrained axes actually changed, so a pure reorder stays silent.
  let prevFields: string[] = [];
  $effect(() => {
    const f = fields;
    values;                                  // a new dataset re-runs this too
    // untrack: this effect WRITES `brushes`, so reading it reactively here would
    // make it its own dependency and loop
    const prev = untrack(() => brushes);
    const byAxis = new Map<string, [number, number]>();
    prevFields.forEach((name, i) => {
      const b = prev[i];
      if (b) byAxis.set(name, b);
    });
    const next = f.map((name) => byAxis.get(name) ?? null);
    const kept = new Set(f.filter((_, i) => next[i]));
    const dropped = [...byAxis.keys()].some((name) => !kept.has(name));
    prevFields = [...f];
    brushes = next;
    if (dropped) applyBrushes(next);
  });

  // Per-axis marginal-density violin over a set of rows (all designs, or a brushed
  // subset for the conditional distribution). Each axis is self-normalized so the
  // shape is visible; `rows === null` means the full population.
  const VBINS = 44;
  function drawViolins(
    ctx: CanvasRenderingContext2D,
    sc: ScaleLinear<number, number>[], yp: number[], rh: number,
    rows: number[] | null, fill: string, stroke: string,
    src: number[][] = values,
  ) {
    const halfMax = halfOf(rh);
    const N = rows ? rows.length : src.length;
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
      if (rows) for (const r of rows) add(src[r][a]);
      else for (let r = 0; r < src.length; r++) add(src[r][a]);
      // 3-tap smoothing for a violin (rather than blocky histogram) look
      const sm = counts.map((c, i) =>
        ((counts[i - 1] ?? c) + 2 * c + (counts[i + 1] ?? c)) / 4);
      const maxC = Math.max(...sm) || 1;
      const y = yp[a];
      // rotated: bins run along X, density becomes thickness in Y
      const xOf = (i: number) => scale(dlo + ((i + 0.5) / VBINS) * span);
      ctx.beginPath();
      for (let i = 0; i < VBINS; i++) {
        const hh = (sm[i] / maxC) * halfMax;
        if (i === 0) ctx.moveTo(xOf(i), y + hh);
        else ctx.lineTo(xOf(i), y + hh);
      }
      for (let i = VBINS - 1; i >= 0; i--) ctx.lineTo(xOf(i), y - (sm[i] / maxC) * halfMax);
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
  // Behind it, a grey track marks the UNCONSTRAINED range, so how much a
  // constraint costs you reads as amber-inside-grey. The grey is only drawn once
  // the two actually differ, otherwise it is redundant ink on every axis.
  function drawFlexBands(
    ctx: CanvasRenderingContext2D,
    sc: ScaleLinear<number, number>[], yp: number[], rh: number,
  ) {
    if (!flexRanges.length && !flexBase.length) return;
    const T = canvasTokens();
    const half = halfOf(rh) + 4;
    ctx.save();
    if (flexBusy) ctx.globalAlpha = 0.55;
    for (let a = 0; a < fields.length; a++) {
      const f = fields[a], y = yp[a];
      const bs = baseBy.get(f), rg = flexBy.get(f);

      if (bs && bs.min != null && bs.max != null) {
        const p = pctLeft(f);
        // p == null → no current range to compare; p >= 99.5 → nothing lost yet
        if (!flexFeasible || (p != null && p < 99.5)) {
          const xLo = sc[a](bs.min), xHi = sc[a](bs.max);
          ctx.fillStyle = T.neutralFill;
          ctx.fillRect(xLo, y - half, xHi - xLo, half * 2);
          ctx.strokeStyle = T.neutralLine;
          ctx.lineWidth = 1;
          for (const x of [xLo, xHi]) {
            ctx.beginPath();
            ctx.moveTo(x, y - half);
            ctx.lineTo(x, y + half);
            ctx.stroke();
          }
        }
      }

      // infeasible constraints: there is no remaining range to draw at all
      if (!flexFeasible || !rg || rg.min == null || rg.max == null) continue;
      const xLo = sc[a](rg.min), xHi = sc[a](rg.max);
      ctx.fillStyle = T.amberSoft;
      ctx.fillRect(xLo, y - half, xHi - xLo, half * 2);
      ctx.strokeStyle = T.amberLine;
      ctx.lineWidth = 1.2;
      for (const x of [xLo, xHi]) {
        ctx.beginPath();
        ctx.moveTo(x, y - half);
        ctx.lineTo(x, y + half);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  // The violins read `marginalValues` when it is supplied (a strict subset of
  // axes is shown, so the marginals are taken in the projection — worldview B)
  // and the base cloud otherwise, when the two coincide anyway.
  const violinSrc = $derived(marginalValues?.length ? marginalValues : values);

  // Conditional distribution under the current brushes, computed IN whichever
  // source the violins are drawn from. `selected` cannot be reused here: it
  // indexes the base cloud, and the projected sample is a different set of rows.
  const violinSel = $derived.by(() => {
    if (!marginalValues?.length) return selected;
    const active = brushes.map((b, a) => ({ b, a })).filter((x) => x.b);
    if (!active.length) return null;
    const rows: number[] = [];
    for (let r = 0; r < marginalValues.length; r++) {
      let ok = true;
      for (const { b, a } of active) {
        const v = marginalValues[r][a];
        if (v < b![0] || v > b![1]) { ok = false; break; }
      }
      if (ok) rows.push(r);
    }
    return rows;
  });

  // Layout + base layer. Writes scales/xPos (for the SVG) but never reads them,
  // so it cannot re-trigger itself. Re-runs on data or size change.
  $effect(() => {
    const f = fields, v = values, w = W, h = H, de = domainExtra, vi = showViolins;
    const fr = flexRanges, fb = flexBase, ff = flexFeasible, fz = flexBusy;
    const vsrc = violinSrc, mb = marginalBusy;
    theme;                                   // repaint when the theme flips
    const T = canvasTokens();
    if (!base || !w || !h || !f.length || !v.length || !de) return;
    const { sc, yp, rh } = buildLayout(w, h);
    scales = sc;
    yPos = yp;
    rowH = rh;
    const ctx = setupCanvas(base, w, h);
    ctx.clearRect(0, 0, w, h);
    if (vi) {
      // exact-range bands behind, then the full-population violin (neutral ghost);
      // the brushed conditional violin is drawn brighter on the highlight layer.
      if (fr.length || fb.length) drawFlexBands(ctx, sc, yp, rh);
      // dimmed while a fresh projection sample is in flight, so a stale
      // distribution never reads as the current one
      if (mb) ctx.globalAlpha = 0.45;
      drawViolins(ctx, sc, yp, rh, null, T.violinFill, T.violinLine, vsrc);
      ctx.globalAlpha = 1;
    } else {
      // faint neutral context for ALL designs (so a selection reads as "the rest")
      ctx.lineWidth = 0.6;
      ctx.strokeStyle = T.neutralFill;
      strokeRows(ctx, v.keys(), sc, yp);
    }
  });

  // Highlight layer, colored by the current field. When a selection is active we
  // color just the selection (over the faint base); otherwise we color all rows
  // as a vivid overview. An optional `overlay` design (e.g. the A→B morph or a
  // hovered pin) is drawn on top as a single bright glowing line.
  // Reads scales/xPos/selected/rowColor/overlay; writes nothing.
  $effect(() => {
    const sel = selected, w = W, h = H, sc = scales, yp = yPos, rh = rowH, rc = rowColor;
    const ov = overlay, stat = staticLines, vi = showViolins;
    const vsrc = violinSrc, vsel = violinSel;
    theme;                                   // repaint when the theme flips
    const T = canvasTokens();
    // null overlayColor => --tick, so the sweep line reads on either surface
    const oc = overlayColor ?? T.tick;
    if (!hl || !w || !h || !sc.length) return;
    const ctx = setupCanvas(hl, w, h);
    ctx.clearRect(0, 0, w, h);
    const hasSel = !!(sel && sel.length);
    if (vi) {
      // conditional distribution: the brushed subset as a bright violin on top of
      // the ghost full-population one (answers "if I fix this, what happens?").
      if (vsel && vsel.length)
        drawViolins(ctx, sc, yp, rh, vsel, T.accent + "4d", T.accent, vsrc);
    } else {
      // non-violin mode: color the selection, or the whole set as a vivid overview.
      const rows: Iterable<number> = hasSel ? sel! : values.keys();
      ctx.lineWidth = hasSel ? 0.9 : 0.5;
      strokeColored(ctx, rows, hasSel ? 0.5 : 0.22, sc, yp, rc);
    }

    const drawLine = (vals: number[], color: string, width: number, glow: number, alpha: number) => {
      if (!vals || vals.length !== fields.length) return;
      ctx.lineWidth = width;
      ctx.strokeStyle = color;
      ctx.shadowColor = color;
      ctx.shadowBlur = glow;
      ctx.globalAlpha = alpha;
      ctx.beginPath();
      for (let a = 0; a < yp.length; a++) {
        const x = sc[a](vals[a]), y = yp[a];
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
      {#if scales[a] !== undefined && yPos[a] !== undefined}
        {@const y = yPos[a]}
        {@const half = halfOf(rowH)}
        {@const cur = flexBy.get(f)}
        {@const p = pctLeft(f)}
        <line class="axis" x1={M.left} y1={y} x2={W - M.right} y2={y} />

        <!-- label gutter (left). Name and the "% left" readout are on separate
             lines here — stacked on one axis head they collided. -->
        <text class="axis-name" x={M.left - 8} y={y - 2}>
          {short(f)}<title>{f}{units[f] ? ` · ${shortUnit(units[f])}` : ""} — drag to filter, click to clear</title>
        </text>
        {#if !flexFeasible}
          <text class="flex-pct bad" x={M.left - 8} y={y + 9}>✕</text>
        {:else if p != null && p < 99.5}
          <text class="flex-pct" x={M.left - 8} y={y + 9} class:busy={flexBusy}>
            {p < 1 ? p.toFixed(1) : p.toFixed(0)}% left
            <title>{(100 - p).toFixed(0)}% of this lever's near-optimal range is ruled out</title>
          </text>
        {/if}

        <!-- the surviving range in numbers, under the row's two ends -->
        {#if flexFeasible && cur?.min != null && cur?.max != null}
          <text class="flex-val start" x={M.left} y={y + half + 13} class:busy={flexBusy}>
            {fmt(cur.min)}
            <title>exact feasible range for {short(f)} under the current constraints (LP, not sample filtering)</title>
          </text>
          <text class="flex-val end" x={W - M.right} y={y + half + 13} class:busy={flexBusy}>
            {fmt(cur.max)}{units[f] ? " " + shortUnit(units[f]) : ""}
          </text>
        {:else}
          <text class="tick start" x={M.left} y={y + half + 13}>{fmt(scales[a].domain()[0])}</text>
          <text class="tick end" x={W - M.right} y={y + half + 13}>
            {fmt(scales[a].domain()[1])}{units[f] ? " " + shortUnit(units[f]) : ""}
          </text>
        {/if}

        {#if showOptimum && optBy.has(f)}
          {@const ox = scales[a](optBy.get(f)!)}
          {#if ox >= M.left && ox <= W - M.right}
            <line class="opt-tick" x1={ox} x2={ox} y1={y - half - 2} y2={y + half + 2}>
              <title>cost optimum: {fmt(optBy.get(f)!)}</title>
            </line>
          {/if}
        {/if}
        {#if brushes[a]}
          <rect
            class="brush"
            x={scales[a](brushes[a]![0])}
            y={y - half - 3}
            width={Math.max(2, scales[a](brushes[a]![1]) - scales[a](brushes[a]![0]))}
            height={half * 2 + 6}
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
  .axis { stroke: var(--axis-line); stroke-width: 1; }
  /* rows are horizontal, so the gutter text is right-aligned against the plot */
  .axis-name {
    fill: var(--accent); font-size: 11px; text-anchor: end; font-weight: 600;
    cursor: pointer;
  }
  .axis-name:hover { text-decoration: underline; }
  .tick, .flex-val {
    font-size: 9px; dominant-baseline: hanging; font-variant-numeric: tabular-nums;
  }
  .tick { fill: var(--muted); }
  .start { text-anchor: start; }
  .end { text-anchor: end; }
  /* flex decorations — amber matches the exact-range band drawn on the canvas */
  .flex-pct { fill: var(--amber); font-size: 9px; text-anchor: end; font-variant-numeric: tabular-nums; }
  .flex-pct.bad { fill: var(--warn); font-size: 11px; }
  .flex-val { fill: var(--amber); opacity: 0.85; }
  .flex-pct.busy, .flex-val.busy { opacity: 0.5; }
  .opt-tick { stroke: var(--tick); stroke-width: 2; opacity: 0.85; }
  .brush { fill: color-mix(in srgb, var(--accent) 18%, transparent); stroke: var(--accent); stroke-width: 1; pointer-events: none; }
</style>
