<script lang="ts">
  import type { FlexRange } from "./api";

  // "Remaining flexibility": per axis, the full near-optimal range (track) vs the
  // exact range still feasible under the current constraints (band), with the
  // optimum marked. Both come from LPs over the polytope, not sample filtering.
  let {
    base = [],
    current = [],
    optimum = [],
    feasible = true,
    busy = false,
  }: {
    base: FlexRange[];
    current: FlexRange[];
    optimum: number[]; // physical value per axis, same order as base
    feasible?: boolean;
    busy?: boolean;
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

  function pct(b: FlexRange, v: number | null): number {
    if (v == null || b.min == null || b.max == null) return 0;
    const t = (v - b.min) / (b.max - b.min || 1);
    return Math.max(0, Math.min(1, t)) * 100;
  }
</script>

<div class="flex-bars" class:busy>
  {#if !feasible}
    <div class="infeasible">⚠ constraints infeasible — no near-optimal design satisfies all of them</div>
  {/if}
  {#each base as b, i}
    {@const cur = current[i] ?? b}
    <div class="row" title="{b.axis}: full range [{fmt(b.min ?? 0)}, {fmt(b.max ?? 0)}] · remaining [{fmt(cur.min ?? 0)}, {fmt(cur.max ?? 0)}]">
      <span class="name">{short(b.axis)}</span>
      <div class="track">
        {#if feasible && cur.min != null && cur.max != null}
          <div
            class="band"
            style="left:{pct(b, cur.min)}%; width:{Math.max(1.5, pct(b, cur.max) - pct(b, cur.min))}%"
          ></div>
        {/if}
        {#if optimum[i] != null}
          <div class="opt" style="left:{pct(b, optimum[i])}%" title="optimum"></div>
        {/if}
      </div>
      <span class="val">{feasible && cur.min != null && cur.max != null ? `${fmt(cur.min)}–${fmt(cur.max)}` : "—"}</span>
    </div>
  {/each}
</div>

<style>
  .flex-bars { display: flex; flex-direction: column; gap: 5px; transition: opacity 0.15s; }
  .flex-bars.busy { opacity: 0.55; }
  .row { display: grid; grid-template-columns: 52px 1fr 74px; align-items: center; gap: 7px; font-size: 10.5px; }
  .name { color: var(--muted); text-align: right; overflow: hidden; text-overflow: ellipsis; }
  .track {
    position: relative; height: 9px; border-radius: 5px;
    background: rgba(255, 255, 255, 0.07);
    border: 1px solid rgba(255, 255, 255, 0.06);
  }
  .band {
    position: absolute; top: 1px; bottom: 1px; border-radius: 4px;
    background: linear-gradient(90deg, rgba(45, 212, 191, 0.55), rgba(45, 212, 191, 0.8));
    box-shadow: 0 0 6px rgba(45, 212, 191, 0.35);
    transition: left 0.25s, width 0.25s;
  }
  .opt {
    position: absolute; top: -2px; bottom: -2px; width: 2px; margin-left: -1px;
    background: #fff; border-radius: 1px;
    box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.6);
  }
  .val { color: var(--fg); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .infeasible { color: #f0a14e; font-size: 11px; }
</style>
