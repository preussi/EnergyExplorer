<script lang="ts">
  import { cellColor, heatGradient } from "./colors";
  import { flipGaps, getShadow, type CornerGaps, type Dependence,
           type DependenceMetric, type FacetCategory, type ShadowPair } from "./api";
  import { CAT, CATEGORIES, DASHED, classifyFacet, GAP_EPS_DEFAULT } from "./facets";
  import { clusterOrder } from "./cluster";

  // Pairwise dependence heatmap over the 9 technology axes. Distance correlation / mutual
  // information catch the nonlinear coupling Pearson misses in this uniform cloud.
  // The matrix is symmetric, so instead of mirroring the numbers we use the two
  // triangles for two different reads of the same pair: the lower-left holds the
  // dependence value (color + number), the upper-right holds the exact 2-D facet
  // outline (LP shadow) — statistical coupling on one side, geometric shape on the
  // other. Axes are ordered by hierarchical clustering on the coupling values, so
  // coupled groups read as blocks on the diagonal. Click a cell to open that pair as an exact Facet —
  // this matrix is the only way into the facet view.
  let {
    dep = null,
    pairs = [],
    metric = "dcor",
    showShapes = true,
    gapEps = GAP_EPS_DEFAULT,
    onpair,
    onmetric,
  }: {
    dep?: Dependence | null;
    /** every pair with its polygon + gaps, from ONE request */
    pairs?: ShadowPair[];
    metric?: DependenceMetric;
    /** colour the upper triangle by facet shape category */
    showShapes?: boolean;
    /** materiality threshold on the corner gaps — a live control, because the
     *  classification is arithmetic over cached gaps (see facets.ts) */
    gapEps?: number;
    onpair?: (x: string, y: string) => void;
    onmetric?: (m: DependenceMetric) => void;
  } = $props();

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;

  // Above this fraction of the scale the cell is light enough (green/yellow end
  // of viridis) that dark ink beats white on it. Measured crossover: 0.475, where
  // white and #10151c both land at ~4.4:1. The old 0.55 left a 3.37:1 worst case.
  const VAL_INK_AT = 0.475;

  const LABELS: Record<DependenceMetric, string> = {
    dcor: "distance correlation", mi: "mutual information", pearson: "Pearson",
  };

  const rawAxes = $derived(dep?.axes ?? []);
  const rawMatrix = $derived<number[][]>(dep ? dep[metric] : []);
  // dependence "strength" used for the color ramp / ordering (Pearson uses magnitude)
  const strength = (v: number) => (metric === "pearson" ? Math.abs(v) : v);

  // Axis order: hierarchical clustering on the coupling values (see cluster.ts).
  // Keyed on the currently-shown metric, so the matrix re-clusters when you switch
  // dcor/MI/Pearson, and on the enabled axes, so it re-clusters on a toggle.
  const order = $derived.by(() => clusterOrder(rawMatrix.map((row) => row.map(strength))));
  const axes = $derived(order.map((i) => rawAxes[i]));
  const matrix = $derived.by(() => order.map((i) => order.map((j) => rawMatrix[i][j])));
  const scaleMax = $derived.by(() => {
    let m = 0;
    for (let i = 0; i < matrix.length; i++)
      for (let j = 0; j < matrix.length; j++)
        if (i !== j) m = Math.max(m, strength(matrix[i][j]));
    return m || 1;
  });
  // Keep the sign at the top of the range: with Pearson selected, -0.997 must not
  // print as "1" (a perfect trade-off read as a perfect co-movement).
  const fmt = (v: number) =>
    Math.abs(v) >= 0.995
      ? (v < 0 ? "-1" : "1")
      : v.toFixed(2).replace(/^0/, "").replace(/^-0/, "-");

  let hover = $state<{ i: number; j: number } | null>(null);

  // ---- heatmap geometry ----
  // GUT is the label gutter on the left/top. The SAME gutter is added on the right
  // and bottom so the cell block is centred inside the viewBox — without it the
  // block sits off to the right of centre by half the gutter, which reads as the
  // whole matrix being pushed sideways in its panel.
  const CELL = 34;
  const GUT = 48;
  const cx = (j: number) => GUT + j * CELL;   // cell left edge
  const size = $derived(GUT * 2 + axes.length * CELL);

  // ---- upper-right triangle: exact 2-D facet outlines (LP shadows) ----
  // Fetch each pair's shadow polygon once and scale it into a local 0..27 box so
  // it can be positioned with a translate (independent of where the axes end up if
  // the seriation reorders them). Keyed by axis-pair name, not cell index, so a
  // reorder reuses cached shapes instead of drawing them in the wrong cell.
  function miniPath(poly: number[][], lo: number, hi: number): string {
    const xs = poly.map((q) => q[0]), ys = poly.map((q) => q[1]);
    const mnx = Math.min(...xs), mxx = Math.max(...xs);
    const mny = Math.min(...ys), mxy = Math.max(...ys);
    const px = (v: number) => (mxx === mnx ? (lo + hi) / 2 : lo + ((v - mnx) / (mxx - mnx)) * (hi - lo));
    const py = (v: number) => (mxy === mny ? (lo + hi) / 2 : hi - ((v - mny) / (mxy - mny)) * (hi - lo));
    return "M" + poly.map((q) => `${px(q[0]).toFixed(1)},${py(q[1]).toFixed(1)}`).join("L") + "Z";
  }
  // Everything below is derived from the ONE batched `pairs` response. The matrix
  // used to fire a getShadow per pair — 36 requests at 9 axes, 276 at the 24-axis
  // cap. `pending` pairs (build budget ran out) are the only ones fetched singly.
  //
  // The API returns each pair once in canonical axis order; a cell may need it as
  // (column, row), so the polygon is transposed and LR/UL swapped when it does.
  const byPair = $derived.by(() => {
    const m = new Map<string, { poly: number[][] | null; gaps: CornerGaps | null }>();
    for (const p of pairs) {
      if (p.polygon) m.set(`${p.x}|${p.y}`, { poly: p.polygon, gaps: p.corner_gaps ?? null });
      if (p.polygon) {
        m.set(`${p.y}|${p.x}`, {
          poly: p.polygon.map((q) => [q[1], q[0]]),
          gaps: p.corner_gaps ? flipGaps(p.corner_gaps) : null,
        });
      }
    }
    return m;
  });

  let lateGaps = $state<Record<string, CornerGaps>>({});
  let latePaths = $state<Record<string, string>>({});
  const facetGaps = $derived.by(() => {
    const out: Record<string, CornerGaps> = { ...lateGaps };
    for (const [k, v] of byPair) if (v.gaps) out[k] = v.gaps;
    return out;
  });
  const facetPaths = $derived.by(() => {
    const out: Record<string, string> = { ...latePaths };
    for (const [k, v] of byPair) if (v.poly) out[k] = miniPath(v.poly, 4, 29);
    return out;
  });

  // fallback for pairs the build budget didn't reach
  const lateReq = new Set<string>();
  $effect(() => {
    for (const p of pairs) {
      if (!p.pending) continue;
      const key = `${p.x}|${p.y}`;
      if (lateReq.has(key)) continue;
      lateReq.add(key);
      getShadow(p.x, p.y, [])
        .then((sh) => {
          if (!sh.polygon?.length) return;
          const flip = sh.polygon.map((q) => [q[1], q[0]]);
          latePaths = { ...latePaths, [key]: miniPath(sh.polygon, 4, 29),
                        [`${p.y}|${p.x}`]: miniPath(flip, 4, 29) };
          if (sh.corner_gaps) {
            lateGaps = { ...lateGaps, [key]: sh.corner_gaps,
                         [`${p.y}|${p.x}`]: flipGaps(sh.corner_gaps) };
          }
        })
        .catch(() => lateReq.delete(key));
    }
  });

  // ---- shape taxonomy ----
  // Labels are derived here from the cached gaps rather than taken from the
  // backend, so moving the threshold re-classifies instantly with no refetch.
  const shapeOf = (x: string, y: string) =>
    classifyFacet(facetGaps[`${x}|${y}`], x, y, gapEps);
  const CAT_ORDER = CATEGORIES;
  const catCounts = $derived.by(() => {
    const c = {} as Record<string, number>;
    for (const k of Object.keys(facetGaps)) {
      const [x, y] = k.split("|");
      // each unordered pair is keyed both ways; count it once
      if (x > y) continue;
      c[classifyFacet(facetGaps[k], x, y, gapEps).category] =
        (c[classifyFacet(facetGaps[k], x, y, gapEps).category] ?? 0) + 1;
    }
    return c;
  });
  const isDashed = (c: string) => DASHED.includes(c as never);
  // The four hued categories always get a key (the taxonomy should be legible
  // even where a category happens not to occur); the two dashed ones appear only
  // when the current threshold actually produces them, so v13 — where neither
  // ever occurs — keeps the legend it had.
  const shownCats = $derived(
    CAT_ORDER.filter((c) => !isDashed(c) || (catCounts[c] ?? 0) > 0));

  // click a legend chip to isolate that category; click again to clear
  let focusCat = $state<FacetCategory | null>(null);
  let explain = $state(false);
  // Each diagram is the unit square with the named corner(s) cut off — the same
  // geometry the classifier measures, so the picture IS the definition.
  const DIAGRAM: Record<string, string> = {
    tradeoff:     "M2,22 L2,2 L14,2 L22,12 L22,22 Z",
    dependency:   "M2,22 L2,2 L22,2 L22,12 L12,22 Z",
    at_least_one: "M2,12 L2,2 L22,2 L22,22 L12,22 Z",
    independent:  "M2,22 L2,2 L22,2 L22,22 Z",
    // the diagonals: band cuts LL+UR (sum pinned), locked cuts UL+LR (difference)
    band:         "M2,2 L12,2 L22,12 L22,22 L12,22 L2,12 Z",
    locked:       "M12,2 L22,2 L22,12 L12,22 L2,22 L2,12 Z",
  };
</script>

<div class="dep">
  <div class="grid-wrap">
    <div class="head">
      <span class="title">pairwise dependence</span>
      <!-- legend inline, not in a side column: a rail beside the matrix pushed it
           off-centre, and it only ever held this one ramp -->
      <span class="legend" title={metric === "pearson" ? "|correlation|" : LABELS[metric]}>
        <span class="muted small">0</span>
        <span class="ramp" style="background:{heatGradient()}"></span>
        <span class="muted small">{scaleMax.toFixed(2)}</span>
      </span>
      <span class="metric-toggle">
        {#each ["dcor", "mi", "pearson"] as m}
          <button class:active={metric === m} onclick={() => onmetric?.(m as DependenceMetric)}>{m}</button>
        {/each}
      </span>
    </div>

    {#if dep && matrix.length}
      <svg class="heat" viewBox="0 0 {size} {size}" preserveAspectRatio="xMidYMid meet">
        {#each axes as a, i}
          <!-- column labels (top, rotated) -->
          <text class="lab" x={cx(i) + 17} y={GUT - 8} transform="rotate(-45 {cx(i) + 17} {GUT - 8})">{short(a)}</text>
          <!-- row labels -->
          <text class="lab row" x={GUT - 6} y={cx(i) + 21}>{short(a)}</text>
        {/each}
        {#each axes as _a, i}
          {#each axes as _b, j}
            {@const v = matrix[i][j]}
            {@const isDiag = i === j}
            {@const isUpper = i < j}
            <rect
              x={cx(j)} y={cx(i)} width={CELL - 1} height={CELL - 1} rx="3"
              class="cell" class:diag={isDiag} class:hot={hover && hover.i === i && hover.j === j}
              fill={isDiag ? "var(--s-03)"
                : isUpper ? (showShapes ? CAT[shapeOf(axes[j], axes[i]).category].color + "1f"
                                        : "var(--s-02)")
                : cellColor(strength(v), 0, scaleMax)}
              class:faded={showShapes && isUpper && focusCat !== null
                && shapeOf(axes[j], axes[i]).category !== focusCat}
              role="button" tabindex="-1"
              aria-label="{short(axes[i])} vs {short(axes[j])}"
              onpointerenter={() => (hover = { i, j })}
              onpointerleave={() => (hover = null)}
              onclick={() => !isDiag && onpair?.(axes[j], axes[i])}
            ></rect>
            {#if isDiag}
              <!-- no glyph on the diagonal -->
            {:else if isUpper}
              {#if facetPaths[`${axes[j]}|${axes[i]}`]}
                {@const cat = showShapes ? shapeOf(axes[j], axes[i]).category : "unknown"}
                <path transform="translate({cx(j)}, {cx(i)})"
                      d={facetPaths[`${axes[j]}|${axes[i]}`]}
                      class="facet-mini"
                      class:dashed={cat === "band" || cat === "locked"}
                      class:faded={showShapes && focusCat !== null && cat !== focusCat}
                      fill="{showShapes ? CAT[cat].color : '#2dd4bf'}26"
                      stroke={showShapes ? CAT[cat].color : '#2dd4bf'}
                      pointer-events="none" />
              {/if}
            {:else}
              <text class="val" class:dark={strength(v) > scaleMax * VAL_INK_AT}
                    x={cx(j) + 16.5} y={cx(i) + 21} pointer-events="none">{fmt(v)}</text>
            {/if}
          {/each}
        {/each}
      </svg>
      <p class="caption">
        {#if hover && hover.i !== hover.j}
          {@const hs = hover.i < hover.j ? shapeOf(axes[hover.j], axes[hover.i]) : null}
          <strong>{short(axes[hover.i])} × {short(axes[hover.j])}</strong> ·
          {#if hs && showShapes}
            <span style="color:{CAT[hs.category].color}">{CAT[hs.category].label}</span> — {hs.detail}{#if hs.borderline} <em>(borderline)</em>{/if}
          {:else}
            {LABELS[metric]} = {matrix[hover.i][hover.j].toFixed(3)} · click → open facet
          {/if}
        {:else}
          <button class="info sm" class:on={explain} aria-label="how to read this matrix"
                  title="how to read this" onclick={() => (explain = !explain)}>i</button>
          <span class="hint-lead">hover a cell · click to open its facet</span>
        {/if}
      </p>
      {#if showShapes}
      <div class="shape-key" role="group" aria-label="facet shape categories">
        {#each shownCats as c}
          <button class="key" class:on={focusCat === c} class:off={focusCat !== null && focusCat !== c}
                  title="{CAT[c].hint} — click to isolate"
                  onclick={() => (focusCat = focusCat === c ? null : c)}>
            <span class="sw" class:dashed={isDashed(c)}
                  style="background:{CAT[c].color}33; border-color:{CAT[c].color}"></span>
            {CAT[c].label}<span class="key-n">{catCounts[c] ?? 0}</span>
          </button>
        {/each}
      </div>
      {/if}

      {#if explain}
        <div class="explain">
          <p class="ex-lead">
            Axes are ordered by hierarchical clustering on the coupling values, so
            coupled groups read as blocks on the diagonal. <strong>Lower-left</strong>
            is the dependence value; <strong>upper-right</strong> is that pair's exact
            2-D facet outline. Click any cell to open it. n = {dep.n} samples.
          </p>
          {#if showShapes}
          <p class="ex-lead">
            A facet is a projection of a convex body, so it is always convex and always
            touches all four sides of its own bounding box. The only thing left to vary
            is <strong>which corners it cannot reach</strong> — which is what these
            six labels name, and why there are exactly six. The number beside each is how far, as a share of both
            ranges at once, you must move off that corner to reach a feasible design.
          </p>
          {#each CAT_ORDER as c}
            <div class="ex-row">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <rect x="2" y="2" width="20" height="20" rx="1" class="ex-box" />
                <path d={DIAGRAM[c]} fill="{CAT[c].color}33" stroke={CAT[c].color}
                      stroke-dasharray={isDashed(c) ? "3 2" : "none"} />
              </svg>
              <div>
                <span style="color:{CAT[c].color}">{CAT[c].label}</span>
                <small>{CAT[c].hint}</small>
              </div>
            </div>
          {/each}
          <p class="ex-foot">
            A label flips at a threshold ({gapEps.toFixed(2)} of the range, adjustable in
            Settings), so borderline calls are marked as such. It is a reporting choice,
            not a measurement limit — the gaps themselves are exact to ~1e-3.
          </p>
          {/if}
        </div>
      {/if}
    {:else}
      <span class="muted small">computing dependence…</span>
    {/if}
  </div>

</div>

<style>
  /* single column: the matrix gets the full width and stays centred whether or
     not the facet panel is docked beside it */
  .dep { display: flex; width: 100%; height: 100%; min-height: 0; padding: 4px; }
  .grid-wrap { flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .head { display: flex; align-items: center; margin-bottom: 6px; gap: 12px; }
  .title { font-size: 13px; color: var(--fg); }
  .metric-toggle { display: flex; gap: 4px; }
  .metric-toggle button {
    font-size: 11px; padding: 2px 9px; border-radius: 6px;
    background: var(--s-03); border: 1px solid var(--s-09); color: var(--muted);
  }
  .metric-toggle button.active { background: var(--accent); color: var(--on-accent); border-color: var(--accent); }

  .heat { flex: 1; min-height: 0; max-height: 100%; overflow: visible; }
  /* --panel, not --panel-glass: the glass is translucent now, and a see-through
     gap let neighbouring cell fills bleed into each other. */
  .cell { stroke: var(--panel); stroke-width: 1; cursor: pointer; }
  .cell.diag { cursor: default; }
  .cell.hot { stroke: var(--tick); stroke-width: 2; }
  /* Literals, not theme tokens: these sit ON a viridis cell whose colour no
     longer depends on the theme, so the readable ink does not either. White is
     used up to VAL_INK_AT and dark ink above it — the crossover of the two
     contrast curves is t = 0.475, where both sit at ~4.4:1. */
  .val { font-size: 10px; fill: #ffffff; text-anchor: middle; opacity: 0.92; }
  .val.dark { fill: #10151c; opacity: 1; }
  .facet-mini { stroke-width: 1.2; stroke-linejoin: round; transition: opacity 0.15s; }
  .facet-mini.dashed { stroke-dasharray: 3 2; }
  /* isolate-a-category: non-matching cells recede rather than disappear, so the
     matrix keeps its shape and you can still see what you filtered out */
  .facet-mini.faded { opacity: 0.12; }
  .cell.faded { opacity: 0.25; }

  .shape-key {
    display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px;
    margin-top: 6px; font-size: 10.5px;
  }
  .hint-lead { color: var(--muted); }
  .key {
    display: inline-flex; align-items: center; gap: 5px;
    padding: 1px 7px 1px 5px; border-radius: 999px; font-size: 10.5px;
    background: var(--s-03); border: 1px solid var(--s-09);
    color: var(--fg); cursor: pointer;
  }
  .key.on { border-color: var(--b-20); background: var(--s-09); }
  .key.off { opacity: 0.45; }
  .sw { width: 10px; height: 10px; border-radius: 3px; border: 1px solid; flex: none; }
  .key-n { color: var(--muted); font-variant-numeric: tabular-nums; }
  /* mirrors .facet-mini.dashed — the swatch must look like the mark it explains */
  .sw.dashed { border-style: dashed; }
  .info.sm { vertical-align: middle; margin-right: 4px; }
  .info {
    width: 15px; height: 15px; flex: none; padding: 0; border-radius: 50%;
    font-size: 9.5px; font-style: italic; font-weight: 700; line-height: 1;
    background: var(--s-05); border: 1px solid var(--b-20);
    color: var(--muted); cursor: pointer;
  }
  .info.on { background: var(--accent); border-color: var(--accent); color: var(--on-accent); }

  .explain {
    margin-top: 8px; padding: 10px 12px; border-radius: 10px;
    background: var(--s-03); border: 1px solid var(--s-09);
  }
  .ex-lead, .ex-foot { font-size: 10.5px; color: var(--muted); line-height: 1.45; margin: 0 0 8px; }
  .ex-foot { margin: 8px 0 0; }
  .ex-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 11px; }
  .ex-row svg { width: 24px; height: 24px; flex: none; }
  .ex-box { fill: none; stroke: var(--b-12); stroke-dasharray: 2 2; }
  .ex-row small { color: var(--muted); font-size: 10px; margin-left: 5px; }
  /* sized to fit GUT (48): the longest short label is "wind-off" ≈ 40px at 9.5 */
  .lab { fill: var(--muted); font-size: 9.5px; }
  .lab.row { text-anchor: end; }
  /* FIXED height + no wrapping. The hover text is longer than the idle text, and
     any reflow here changes .heat's flex height, which visibly shifts the matrix
     as you move across cells. */
  .caption {
    font-size: 11px; color: var(--muted); margin: 6px 0 0;
    height: 16px; line-height: 16px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  .legend { display: flex; align-items: center; gap: 5px; margin-left: auto; }
  .ramp { width: 76px; height: 9px; border-radius: 3px; flex: none; }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
</style>
