<script lang="ts">
  import { colorFor } from "./colors";
  import type { Dependence, DependenceMetric, ShadowPair } from "./api";

  // Pairwise dependence heatmap over the 10 axes. Distance correlation / mutual
  // information catch the nonlinear coupling Pearson misses in this uniform cloud.
  // Click a cell to open that pair as an exact Facet. The lower plot cross-checks
  // the *statistical* dependence (dCor) against the *geometric* facet structure
  // (1 - boxiness): they should agree, which validates the sampler.
  let {
    dep = null,
    pairs = [],
    metric = "dcor",
    onpair,
    onmetric,
  }: {
    dep?: Dependence | null;
    pairs?: ShadowPair[];
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

  const axes = $derived(dep?.axes ?? []);
  const matrix = $derived<number[][]>(dep ? dep[metric] : []);
  // dependence "strength" used for the color ramp (Pearson uses magnitude)
  const strength = (v: number) => (metric === "pearson" ? Math.abs(v) : v);
  const scaleMax = $derived.by(() => {
    let m = 0;
    for (let i = 0; i < matrix.length; i++)
      for (let j = 0; j < matrix.length; j++)
        if (i !== j) m = Math.max(m, strength(matrix[i][j]));
    return m || 1;
  });
  const fmt = (v: number) => (Math.abs(v) >= 0.995 ? "1" : v.toFixed(2).replace(/^0/, "").replace(/^-0/, "-"));

  let hover = $state<{ i: number; j: number } | null>(null);

  // ---- validation scatter: dCor (statistical) vs 1 - boxiness (geometric) ----
  const boxMap = $derived.by(() => {
    const m = new Map<string, number>();
    for (const p of pairs) if (p.boxiness != null) m.set(`${p.x}|${p.y}`, p.boxiness);
    return m;
  });
  const valPts = $derived.by(() => {
    if (!dep) return [] as { dcor: number; interest: number; x: string; y: string }[];
    const out: { dcor: number; interest: number; x: string; y: string }[] = [];
    for (let i = 0; i < axes.length; i++)
      for (let j = i + 1; j < axes.length; j++) {
        const b = boxMap.get(`${axes[i]}|${axes[j]}`) ?? boxMap.get(`${axes[j]}|${axes[i]}`);
        if (b == null) continue;
        out.push({ dcor: dep.dcor[i][j], interest: 1 - b, x: axes[i], y: axes[j] });
      }
    return out;
  });
  const VW = 230, VH = 150, VM = { l: 34, r: 10, t: 10, b: 26 };
  const vx = (d: number) => VM.l + d * (VW - VM.l - VM.r) / 0.6;          // dcor 0..0.6
  const vy = (d: number) => VH - VM.b - d * (VH - VM.t - VM.b) / 1.0;     // interest 0..1
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
            <rect
              x={70 + j * 34} y={70 + i * 34} width="33" height="33" rx="3"
              class="cell" class:diag={isDiag} class:hot={hover && hover.i === i && hover.j === j}
              fill={isDiag ? "rgba(255,255,255,0.03)" : colorFor(strength(v), 0, scaleMax, false)}
              role="button" tabindex="-1"
              aria-label="{short(axes[i])} vs {short(axes[j])}"
              onpointerenter={() => (hover = { i, j })}
              onpointerleave={() => (hover = null)}
              onclick={() => !isDiag && onpair?.(axes[j], axes[i])}
            ></rect>
            {#if !isDiag}
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
          click a cell to open its exact 2-D facet · n = {dep.n} samples
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

    <div class="validation">
      <span class="muted small">statistical vs geometric</span>
      <svg viewBox="0 0 {VW} {VH}">
        <line x1={VM.l} y1={VH - VM.b} x2={VW - VM.r} y2={VH - VM.b} class="ax" />
        <line x1={VM.l} y1={VM.t} x2={VM.l} y2={VH - VM.b} class="ax" />
        {#each valPts as p}
          <circle cx={vx(p.dcor)} cy={vy(p.interest)} r="3" class="vdot">
            <title>{short(p.x)} × {short(p.y)} · dCor {p.dcor.toFixed(2)} · interest {(p.interest * 100).toFixed(0)}%</title>
          </circle>
        {/each}
        <text class="vax" x={(VM.l + VW - VM.r) / 2} y={VH - 4} text-anchor="middle">distance correlation →</text>
        <text class="vax" transform="rotate(-90 10 {(VM.t + VH - VM.b) / 2})" x="10" y={(VM.t + VH - VM.b) / 2} text-anchor="middle">1 − boxiness →</text>
      </svg>
      <p class="muted small">Each dot is an axis pair. Agreement (up-right trend) means the
        sampled cloud's dependence matches the polytope's exact facet structure.</p>
    </div>
  </aside>
</div>

<style>
  .dep { display: grid; grid-template-columns: 1fr 250px; gap: 16px; width: 100%; height: 100%; min-height: 0; padding: 4px; }
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
  .validation svg { width: 100%; height: auto; }
  .ax { stroke: rgba(255, 255, 255, 0.18); }
  .vdot { fill: var(--accent); fill-opacity: 0.75; }
  .vax { fill: var(--muted); font-size: 10px; }
  .muted { color: var(--muted); }
  .small { font-size: 11px; }
</style>
