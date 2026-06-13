<script lang="ts">
  // Star-coordinates control: each technology gets a draggable 2-D anchor vector;
  // the map shows each design at the anchor-weighted sum of its normalized values.
  // Linear → preserves convexity, facets and the cost gradient (unlike RadViz).
  let {
    anchors = [],
    labels = [],
    touring = false,
    onchange,
    ontour,
    onresetPca,
    onresetCircle,
  }: {
    anchors: [number, number][];
    labels: string[];
    touring?: boolean;
    onchange?: (anchors: [number, number][]) => void;
    ontour?: () => void;
    onresetPca?: () => void;
    onresetCircle?: () => void;
  } = $props();

  const SIZE = 188;
  const C = SIZE / 2;
  const R = SIZE / 2 - 16;

  let svg: SVGSVGElement;
  let drag = $state<number | null>(null);

  function toLocal(e: PointerEvent): [number, number] {
    const r = svg.getBoundingClientRect();
    return [(e.clientX - r.left - C) / R, -(e.clientY - r.top - C) / R];
  }
  function onDown(i: number, e: PointerEvent) {
    drag = i;
    (e.target as Element).setPointerCapture?.(e.pointerId);
  }
  function onMove(e: PointerEvent) {
    if (drag === null) return;
    let [x, y] = toLocal(e);
    const m = Math.hypot(x, y);
    if (m > 1) { x /= m; y /= m; }
    const next = anchors.map((a, j) => (j === drag ? ([x, y] as [number, number]) : a));
    onchange?.(next);
  }
  function onUp() { drag = null; }

  const px = (a: [number, number]) => C + a[0] * R;
  const py = (a: [number, number]) => C - a[1] * R;
</script>

<div class="wheel">
  <svg
    bind:this={svg}
    width={SIZE} height={SIZE} viewBox="0 0 {SIZE} {SIZE}"
    onpointermove={onMove} onpointerup={onUp} onpointerleave={onUp}
    role="presentation"
  >
    <circle cx={C} cy={C} r={R} class="rim" />
    <circle cx={C} cy={C} r={R / 2} class="rim faint" />
    {#each anchors as a, i}
      <line x1={C} y1={C} x2={px(a)} y2={py(a)} class="vec" />
      <circle
        cx={px(a)} cy={py(a)} r="6.5" class="handle"
        class:dragging={drag === i}
        role="slider" tabindex="-1" aria-label="anchor {labels[i]}" aria-valuenow={0}
        onpointerdown={(e) => onDown(i, e)}
      >
        <title>{labels[i]} — drag to steer the projection</title>
      </circle>
      <text
        x={C + a[0] * (R + 11)} y={C - a[1] * (R + 11)}
        class="lab" text-anchor={a[0] < -0.25 ? "end" : a[0] > 0.25 ? "start" : "middle"}
        dominant-baseline="middle"
      >{labels[i]}</text>
    {/each}
  </svg>
  <div class="row">
    <button class="mini" class:active={touring} onclick={() => ontour?.()}>{touring ? "⏸ tour" : "▶ tour"}</button>
    <button class="mini" onclick={() => onresetPca?.()}>PCA</button>
    <button class="mini" onclick={() => onresetCircle?.()}>circle</button>
  </div>
</div>

<style>
  .wheel { display: flex; flex-direction: column; align-items: center; gap: 6px; }
  svg { touch-action: none; overflow: visible; }
  .rim { fill: none; stroke: rgba(255, 255, 255, 0.14); }
  .rim.faint { stroke: rgba(255, 255, 255, 0.06); }
  .vec { stroke: rgba(45, 212, 191, 0.55); stroke-width: 1.4; }
  .handle {
    fill: #10151c; stroke: var(--accent); stroke-width: 2;
    cursor: grab;
  }
  .handle:hover, .handle.dragging { fill: var(--accent); cursor: grabbing; }
  .lab {
    fill: var(--fg); font-size: 9.5px;
    paint-order: stroke; stroke: rgba(0, 0, 0, 0.8); stroke-width: 2.5px;
    pointer-events: none;
  }
  .row { display: flex; gap: 6px; }
  .mini { font-size: 11px; padding: 3px 9px; border-radius: 6px; }
  .mini.active { background: var(--accent); color: #06241f; border-color: var(--accent); }
</style>
