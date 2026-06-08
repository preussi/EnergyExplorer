<script lang="ts">
  import { onMount } from "svelte";
  import createScatterplot from "regl-scatterplot";
  import { scaleLinear } from "d3-scale";
  import { RAMP, PALETTE } from "./colors";

  let {
    points = [],
    color = [],
    categorical = false,
    optimum = null,
    showOptimum = true,
    clusters = [],
    showClusters = true,
    selected = null,
    mode = "pan",
    onhover,
    onselect,
  }: {
    points: number[][];
    color: number[];
    categorical?: boolean;
    optimum?: number[] | null;
    showOptimum?: boolean;
    clusters?: { x: number; y: number; label: string; count: number }[];
    showClusters?: boolean;
    selected?: number[] | null;
    mode?: "pan" | "select";
    onhover?: (index: number | null) => void;
    onselect?: (indices: number[]) => void;
  } = $props();

  let container: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let scatter: any = null;
  let optPx = $state<[number, number] | null>(null);
  let clusterPx = $state<{ x: number; y: number; label: string; count: number }[]>([]);

  // d3 scales mapping normalized [-1,1] data space -> screen pixels. The library
  // keeps these updated as the user pans/zooms.
  const xScale = scaleLinear().domain([-1, 1]);
  const yScale = scaleLinear().domain([-1, 1]);

  // Data-space normalization (recomputed per dataset), shared by points + optimum.
  let nx = (v: number) => v;
  let ny = (v: number) => v;

  function buildPoints(): number[][] {
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    let minX = Math.min(...xs), maxX = Math.max(...xs);
    let minY = Math.min(...ys), maxY = Math.max(...ys);
    if (optimum) {
      minX = Math.min(minX, optimum[0]); maxX = Math.max(maxX, optimum[0]);
      minY = Math.min(minY, optimum[1]); maxY = Math.max(maxY, optimum[1]);
    }
    const pad = 0.05;
    nx = (v: number) => (((v - minX) / (maxX - minX || 1)) * 2 - 1) * (1 - pad);
    ny = (v: number) => (((v - minY) / (maxY - minY || 1)) * 2 - 1) * (1 - pad);

    // regl-scatterplot colors by `valueA` = the 3rd column. Categorical wants
    // integer category ids; continuous wants a value normalized to [0, 1].
    const minC = Math.min(...color), maxC = Math.max(...color);
    return points.map((p, i) => {
      const valueA = categorical
        ? (color[i] | 0)
        : color.length ? (color[i] - minC) / (maxC - minC || 1) : 0.5;
      return [nx(p[0]), ny(p[1]), valueA, 0];
    });
  }

  // Nudge overlapping cluster labels apart and keep them inside the plot, leaving
  // margins on the left/bottom for the axis captions that sit on those borders.
  function declutter(
    items: { x: number; y: number; label: string; count: number }[],
  ) {
    const charW = 7.6, padX = 40, hh = 11, gap = 6;
    const mLeft = 26, mBottom = 24, mEdge = 6; // axis-text margins
    const w = container?.clientWidth ?? 800;
    const ht = container?.clientHeight ?? 800;
    const boxes = items.map((it) => ({ ...it, w: it.label.length * charW + padX }));

    for (let iter = 0; iter < 90; iter++) {
      let moved = false;
      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          const dx = b.x - a.x, dy = b.y - a.y;
          const ox = (a.w + b.w) / 2 + gap - Math.abs(dx);
          const oy = 2 * hh + gap - Math.abs(dy);
          if (ox > 0 && oy > 0) {
            if (oy < ox) { const p = (oy / 2) * (dy < 0 ? -1 : 1); a.y -= p; b.y += p; }
            else { const p = (ox / 2) * (dx < 0 ? -1 : 1); a.x -= p; b.x += p; }
            moved = true;
          }
        }
      }
      if (!moved) break;
    }
    for (const b of boxes) {
      b.x = Math.max(b.w / 2 + mLeft, Math.min(w - b.w / 2 - mEdge, b.x));
      b.y = Math.max(hh + mEdge, Math.min(ht - hh - mBottom, b.y));
    }
    return boxes;
  }

  function positionOverlays() {
    if (!scatter) return;
    const xs = scatter.get("xScale") ?? xScale;
    const ys = scatter.get("yScale") ?? yScale;
    optPx = showOptimum && optimum ? [xs(nx(optimum[0])), ys(ny(optimum[1]))] : null;
    clusterPx = showClusters
      ? declutter(
          clusters.map((c) => ({
            x: xs(nx(c.x)), y: ys(ny(c.y)), label: c.label, count: c.count,
          })),
        )
      : [];
  }

  async function redraw() {
    if (!scatter || !points.length) return;
    await scatter.set({
      colorBy: "valueA",
      pointColor: categorical ? PALETTE : RAMP,
      pointColorActive: categorical ? PALETTE : RAMP,
      pointColorHover: categorical ? PALETTE : RAMP,
      zDataType: categorical ? "categorical" : "continuous",
    });
    await scatter.draw(buildPoints());
    positionOverlays();
  }

  export function reset() {
    scatter?.reset();
  }

  onMount(() => {
    scatter = createScatterplot({
      canvas,
      width: "auto",
      height: "auto",
      pointSize: 5,
      pointSizeSelected: 2,
      opacity: 0.66,
      backgroundColor: [0, 0, 0, 0], // transparent: let the page backdrop show through
      xScale,
      yScale,
      lassoOnLongPress: false,
      // dim non-selected points so a linked selection stands out
      opacityInactiveScale: 0.12,
      lassoColor: "#2dd4bf",
    });
    scatter.subscribe("pointOver", (i: number) => onhover?.(i));
    scatter.subscribe("pointOut", () => onhover?.(null));
    scatter.subscribe("view", positionOverlays);
    scatter.subscribe("select", ({ points: pts }: { points: number[] }) => onselect?.(pts));
    scatter.subscribe("deselect", () => onselect?.([]));
    scatter.set({ mouseMode: mode === "select" ? "lasso" : "panZoom" });
    redraw();

    // The wrapper is sized by CSS (largest square that fits the pane);
    // reposition the optimum overlay whenever that square changes.
    const ro = new ResizeObserver(() => requestAnimationFrame(positionOverlays));
    ro.observe(container);
    return () => { ro.disconnect(); scatter?.destroy(); };
  });

  // Redraw when data / encoding changes.
  $effect(() => {
    points; color; categorical; optimum;
    if (scatter) redraw();
  });
  // Reposition overlays when their visibility / data toggles.
  $effect(() => {
    showOptimum; showClusters; clusters;
    if (scatter) positionOverlays();
  });
  // Apply a selection coming from outside (e.g. parallel-coords brush) without
  // re-emitting a 'select' event.
  $effect(() => {
    if (!scatter) return;
    if (selected && selected.length) scatter.select(selected, { preventEvent: true });
    else scatter.deselect({ preventEvent: true });
  });
  // Pan vs lasso-select mode.
  $effect(() => {
    scatter?.set({ mouseMode: mode === "select" ? "lasso" : "panZoom" });
  });
</script>

<div class="wrap" bind:this={container}>
  <canvas bind:this={canvas}></canvas>
  {#each clusterPx as c}
    <div class="cluster" style="left:{c.x}px; top:{c.y}px">
      <span class="dot"></span>
      <span class="clabel">{c.label}</span>
    </div>
  {/each}
  {#if optPx}
    <div
      class="optimum"
      style="left:{optPx[0]}px; top:{optPx[1]}px"
      title="Cost optimum (u*)"
    ></div>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    /* borderless, fills the whole stage, sits straight on the backdrop */
    width: 100%;
    height: 100%;
  }
  canvas { width: 100%; height: 100%; display: block; }
  .optimum {
    position: absolute;
    width: 16px;
    height: 16px;
    margin: -8px 0 0 -8px;
    border: 2.5px solid #fff;
    border-radius: 50%;
    box-shadow: 0 0 0 1.5px #000, 0 0 6px rgba(0, 0, 0, 0.7);
    pointer-events: none;
  }
  .optimum::after {
    content: "";
    position: absolute;
    inset: 5px;
    background: #fff;
    border-radius: 50%;
  }

  .cluster {
    position: absolute;
    transform: translate(-50%, -50%);
    display: flex;
    align-items: center;
    gap: 5px;
    pointer-events: none;
    white-space: nowrap;
  }
  .cluster .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: rgba(255, 255, 255, 0.9);
    box-shadow: 0 0 0 2px rgba(0, 0, 0, 0.55), 0 0 8px rgba(45, 212, 191, 0.6);
    flex: none;
  }
  .cluster .clabel {
    font-size: 11px;
    font-weight: 600;
    color: #eef4f2;
    background: rgba(10, 14, 20, 0.7);
    border: 1px solid rgba(45, 212, 191, 0.35);
    border-radius: 6px;
    padding: 1px 6px;
    backdrop-filter: blur(2px);
    text-shadow: 0 1px 2px rgba(0, 0, 0, 0.9);
  }
</style>
