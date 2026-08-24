<script lang="ts">
  // Small star/radar plot of one design's 9 technology values, normalized
  // against the full-space per-technology ranges so glyphs are comparable.
  let {
    values = [],
    ranges = [],
    labels = [],
    color = "#7adfff",
    size = 108,
  }: {
    values: number[]; // 9 technology values (physical units)
    ranges: { min: number; max: number }[]; // per-technology full-space range
    labels: string[]; // short names, same order
    color?: string;
    size?: number;
  } = $props();

  const cx = $derived(size / 2);
  const r = $derived(size / 2 - 10);

  function norm(i: number): number {
    const rg = ranges[i];
    if (!rg) return 0;
    const t = (values[i] - rg.min) / (rg.max - rg.min || 1);
    return Math.max(0.04, Math.min(1, t)); // floor so 0 is still visible
  }
  function vertex(i: number, t: number): [number, number] {
    const a = (i / values.length) * 2 * Math.PI - Math.PI / 2;
    return [cx + Math.cos(a) * r * t, cx + Math.sin(a) * r * t];
  }
  const polygon = $derived(
    values.map((_, i) => vertex(i, norm(i)).join(",")).join(" "),
  );
</script>

<svg width={size} height={size} viewBox="0 0 {size} {size}">
  <!-- grid rings -->
  {#each [0.33, 0.66, 1] as t}
    <polygon
      points={values.map((_, i) => vertex(i, t).join(",")).join(" ")}
      class="ring"
    />
  {/each}
  <!-- spokes -->
  {#each values as _, i}
    <line x1={cx} y1={cx} x2={vertex(i, 1)[0]} y2={vertex(i, 1)[1]} class="spoke">
      <title>{labels[i]}: {values[i]?.toPrecision(4)}</title>
    </line>
  {/each}
  <!-- the design -->
  <polygon points={polygon} fill={color} fill-opacity="0.22" stroke={color} stroke-width="1.6" />
  {#each values as _, i}
    <circle cx={vertex(i, norm(i))[0]} cy={vertex(i, norm(i))[1]} r="2" fill={color}>
      <title>{labels[i]}: {values[i]?.toPrecision(4)}</title>
    </circle>
  {/each}
</svg>

<style>
  /* --grid, not --s-09: the scrims are SURFACE washes, and at their alpha a
     hairline on white came out at ~1.08:1, i.e. the whole web vanished in light
     mode. --grid is the token for chart scaffolding and is tuned per theme. */
  .ring { fill: none; stroke: var(--grid); }
  .spoke { stroke: var(--grid); }
</style>
