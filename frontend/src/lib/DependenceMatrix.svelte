<script lang="ts">
  import { colorFor } from "./colors";
  import { getShadow, type Dependence, type DependenceMetric } from "./api";
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
    metric = "dcor",
    onpair,
    onmetric,
  }: {
    dep?: Dependence | null;
    metric?: DependenceMetric;
    onpair?: (x: string, y: string) => void;
    onmetric?: (m: DependenceMetric) => void;
  } = $props();

  const SHORT: Record<string, string> = {
    photovoltaics: "PV", wind_offshore: "wind-off", wind_onshore: "wind-on",
    electrolysis: "electro", net_present_cost: "cost",
  };
  const short = (s: string) => SHORT[s] ?? s;

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
  let facetPaths = $state<Record<string, string>>({});
  const facetReq = new Set<string>();
  $effect(() => {
    const ax = axes;
    for (let i = 0; i < ax.length; i++)
      for (let j = i + 1; j < ax.length; j++) {
        const key = `${ax[j]}|${ax[i]}`; // x = column axis, y = row axis
        if (facetReq.has(key)) continue;
        facetReq.add(key);
        getShadow(ax[j], ax[i], [])
          .then((s) => {
            if (!s.polygon.length) return;
            facetPaths = { ...facetPaths, [key]: miniPath(s.polygon, 4, 29) };
          })
          .catch(() => facetReq.delete(key));
      }
  });

</script>

<div class="dep">
  <div class="grid-wrap">
    <div class="head">
      <span class="title">pairwise dependence · {LABELS[metric]}</span>
      <span class="metric-toggle">
        {#each ["dcor", "mi", "pearson"] as m}
          <button class:active={metric === m} onclick={() => onmetric?.(m as DependenceMetric)}>{m}</button>
        {/each}
      </span>
    </div>

    {#if dep && matrix.length}
      <svg class="heat" viewBox="0 0 {70 + axes.length * 34} {70 + axes.length * 34}" preserveAspectRatio="xMidYMid meet">
        {#each axes as a, i}
          <!-- column labels (top, rotated) -->
          <text class="lab" x={70 + i * 34 + 17} y="62" transform="rotate(-45 {70 + i * 34 + 17} 62)">{short(a)}</text>
          <!-- row labels -->
          <text class="lab row" x="64" y={70 + i * 34 + 21}>{short(a)}</text>
        {/each}
        {#each axes as _a, i}
          {#each axes as _b, j}
            {@const v = matrix[i][j]}
            {@const isDiag = i === j}
            {@const isUpper = i < j}
            <rect
              x={70 + j * 34} y={70 + i * 34} width="33" height="33" rx="3"
              class="cell" class:diag={isDiag} class:hot={hover && hover.i === i && hover.j === j}
              fill={isDiag ? "rgba(255,255,255,0.03)" : isUpper ? "rgba(255,255,255,0.02)" : colorFor(strength(v), 0, scaleMax, false)}
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
                <path transform="translate({70 + j * 34}, {70 + i * 34})"
                      d={facetPaths[`${axes[j]}|${axes[i]}`]} class="facet-mini" pointer-events="none" />
              {/if}
            {:else}
              <text class="val" class:dark={strength(v) > scaleMax * 0.55}
                    x={70 + j * 34 + 16.5} y={70 + i * 34 + 21} pointer-events="none">{fmt(v)}</text>
            {/if}
          {/each}
        {/each}
      </svg>
      <p class="caption">
        {#if hover && hover.i !== hover.j}
          <strong>{short(axes[hover.i])} × {short(axes[hover.j])}</strong> ·
          {LABELS[metric]} = {matrix[hover.i][hover.j].toFixed(3)} · click → open facet
        {:else}
          clustered by coupling · lower-left = dependence value · upper-right = exact 2-D facet outline · click any cell to open it · n = {dep.n} samples
        {/if}
      </p>
    {:else}
      <span class="muted small">computing dependence…</span>
    {/if}
  </div>

  <aside class="side">
    <div class="legend">
      <span class="muted small">{metric === "pearson" ? "|correlation|" : LABELS[metric]}</span>
      <div class="ramp"></div>
      <div class="ramp-labels"><span>0</span><span>{scaleMax.toFixed(2)}</span></div>
    </div>
  </aside>
</div>

<style>
  /* the side rail only carries the color legend now */
  .dep { display: grid; grid-template-columns: 1fr 110px; gap: 16px; width: 100%; height: 100%; min-height: 0; padding: 4px; }
  .grid-wrap { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; gap: 12px; }
  .title { font-size: 13px; color: var(--fg); }
  .metric-toggle { display: flex; gap: 4px; }
  .metric-toggle button {
    font-size: 11px; padding: 2px 9px; border-radius: 6px;
    background: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.1); color: var(--muted);
  }
  .metric-toggle button.active { background: var(--accent); color: #06241f; border-color: var(--accent); }

  .heat { flex: 1; min-height: 0; max-height: 100%; overflow: visible; }
  .cell { stroke: rgba(10, 14, 20, 0.6); stroke-width: 1; cursor: pointer; }
  .cell.diag { cursor: default; }
  .cell.hot { stroke: #fff; stroke-width: 2; }
  .val { font-size: 10px; fill: #eef4f2; text-anchor: middle; opacity: 0.9; }
  .val.dark { fill: #06241f; }
  .facet-mini { fill: rgba(45, 212, 191, 0.14); stroke: var(--accent); stroke-width: 1.2; stroke-linejoin: round; }
  .lab { fill: var(--muted); font-size: 11px; }
  .lab.row { text-anchor: end; }
  .caption { font-size: 11px; color: var(--muted); margin: 6px 0 0; min-height: 16px; }

  .side { display: flex; flex-direction: column; gap: 18px; min-height: 0; }
  .legend { display: flex; flex-direction: column; gap: 4px; }
  .ramp {
    height: 12px; border-radius: 3px;
    background: linear-gradient(to right, #440154, #3b528b, #21918c, #5ec962, #fde725);
  }
  .ramp-labels { display: flex; justify-content: space-between; font-size: 10px; color: var(--muted); }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
</style>
