<script lang="ts">
  import { onMount } from "svelte";
  import createScatterplot from "regl-scatterplot";
  import { scaleLinear } from "d3-scale";
  import { contourDensity } from "d3-contour";
  import { palette, ramp, themeToken } from "./colors";

  export interface PinMarker {
    id: number;
    letter: string;
    point: [number, number];
    color: string;
  }
  export interface SpokeMarker {
    key: string;
    x: number;
    y: number;
    label: string;
  }

  let {
    points = [],
    color = [],
    categorical = false,
    optimum = null,
    showOptimum = true,
    clusters = [],
    showClusters = true,
    compass = null,
    showCompass = false,
    showDensity = false,
    spokes = [],
    showSpokes = false,
    pins = [],
    path = null,
    walkChains = 0,
    walkActive = false,
    selected = null,
    mode = "pan",
    inset = null,
    draggableMarkers = false,
    themeTick = 0,
    onhover,
    onselect,
    onpin,
    onspoke,
    onmarkerdrag,
  }: {
    points: number[][];
    color: number[];
    categorical?: boolean;
    optimum?: number[] | null;
    showOptimum?: boolean;
    clusters?: { x: number; y: number; label: string; count: number }[];
    showClusters?: boolean;
    compass?: { dx: number; dy: number; label: string }[] | null;
    showCompass?: boolean;
    showDensity?: boolean;
    spokes?: SpokeMarker[];
    showSpokes?: boolean;
    pins?: PinMarker[];
    path?: { a: [number, number]; b: [number, number]; t: number } | null;
    walkChains?: number;
    walkActive?: boolean;
    selected?: number[] | null;
    mode?: "pan" | "select";
    /** Pixels of the canvas that are covered by floating panels. The canvas runs
     *  edge-to-edge under the top bar and the Profiles rail so there is no seam
     *  between them, but the DATA must not: without this the cloud centres on the
     *  full canvas and ~20% of it hides under the rail. Biasing the normalizers
     *  (rather than nudging the camera) keeps points, contours, pins, overlays and
     *  the hit-test consistent, since they all go through nx/ny. */
    inset?: { left: number; top: number; right: number; bottom: number } | null;
    draggableMarkers?: boolean;   // star mode: drag the optimum / pins to rotate the projection
    /** bumped on a theme change: regl bakes the colour maps at set() time and the
     *  walk overlay is a canvas, so both must be re-pushed */
    themeTick?: number;
    onhover?: (index: number | null) => void;
    onselect?: (indices: number[]) => void;
    onpin?: (index: number) => void;
    onspoke?: (key: string) => void;
    onmarkerdrag?: (kind: string, id: number, dataX: number, dataY: number) => void;
  } = $props();

  let container: HTMLDivElement;
  let canvas: HTMLCanvasElement;
  let walkCanvas: HTMLCanvasElement;
  let scatter: any = null;

  // ---- screen-space overlay state (written only by positionOverlays) ----
  let optPx = $state<[number, number] | null>(null);
  let clusterPx = $state<{ x: number; y: number; label: string; count: number }[]>([]);
  let pinPx = $state<{ x: number; y: number; letter: string; color: string; id: number }[]>([]);
  let spokePx = $state<{ x1: number; y1: number; x2: number; y2: number; label: string; key: string }[]>([]);
  let arrowPx = $state<{ x1: number; y1: number; x2: number; y2: number; label: string }[]>([]);
  let pathPx = $state<{ x1: number; y1: number; x2: number; y2: number; mx: number; my: number } | null>(null);
  let contourTf = $state("");
  let contourPaths = $state<{ d: string; o: number }[]>([]);

  const xScale = scaleLinear().domain([-1, 1]);
  const yScale = scaleLinear().domain([-1, 1]);

  // data-space normalization (recomputed per dataset)
  const PAD = 0.05;
  // centre offset + half-span of the visible window, in clip units
  let cOff: [number, number] = [0, 0];
  let cSpan: [number, number] = [1, 1];
  /** [centre, halfSpan] in clip units of the window left after `a`/`b` px are
   *  covered at the two ends of a `total`-px axis. No cover => [0, 1]. */
  function clipWindow(a: number, b: number, total: number): [number, number] {
    if (!total || a + b >= total) return [0, 1];
    return [(a - b) / total, (total - a - b) / total];
  }
  let nx = (v: number) => v;
  let ny = (v: number) => v;
  let dataCentroid: [number, number] = [0, 0];
  let dataSpan = 1;
  // raw data bounds, kept so screen pixels can be inverted back to data space
  // (for drag-to-rotate in star mode)
  let bx0 = 0, bx1 = 1, by0 = 0, by1 = 1;

  const CONTOUR_GRID = 512;

  // Recompute the data->clip normalizers synchronously (shared by point upload,
  // overlay positioning and contour computation — whichever runs first).
  function updateNormalizers() {
    if (!points.length) return;
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    let minX = Math.min(...xs), maxX = Math.max(...xs);
    let minY = Math.min(...ys), maxY = Math.max(...ys);
    if (optimum) {
      minX = Math.min(minX, optimum[0]); maxX = Math.max(maxX, optimum[0]);
      minY = Math.min(minY, optimum[1]); maxY = Math.max(maxY, optimum[1]);
    }
    // Map the data into the VISIBLE sub-rectangle of the canvas rather than the
    // whole of it (see the `inset` prop). cOff is the visible centre in clip
    // units, cSpan its half-width; with no inset these are 0 and 1, i.e. exactly
    // the old behaviour.
    const [ox, sx] = clipWindow(inset?.left ?? 0, inset?.right ?? 0, container?.clientWidth ?? 0);
    // y is flipped in clip space: a `top` inset moves the centre DOWN in data
    // terms, which is -y here.
    const [oyRaw, sy] = clipWindow(inset?.top ?? 0, inset?.bottom ?? 0, container?.clientHeight ?? 0);
    const oy = -oyRaw;
    cOff = [ox, oy]; cSpan = [sx, sy];
    nx = (v: number) => ox + ((((v - minX) / (maxX - minX || 1)) * 2 - 1) * (1 - PAD)) * sx;
    ny = (v: number) => oy + ((((v - minY) / (maxY - minY || 1)) * 2 - 1) * (1 - PAD)) * sy;
    dataCentroid = [(minX + maxX) / 2, (minY + maxY) / 2];
    dataSpan = Math.min(maxX - minX, maxY - minY) || 1;
    bx0 = minX; bx1 = maxX; by0 = minY; by1 = maxY;
  }

  // invert a screen pixel (relative to the container) back to data coordinates —
  // the reverse of scales(); used by drag-to-rotate. Returns null until ready.
  function invScales(): [(px: number) => number, (py: number) => number] | null {
    const xs = scatter?.get("xScale");
    const ys = scatter?.get("yScale");
    if (!xs || !ys) return null;
    const nxInv = (c: number) =>
      bx0 + ((((c - cOff[0]) / cSpan[0]) / (1 - PAD) + 1) / 2) * (bx1 - bx0);
    const nyInv = (c: number) =>
      by0 + ((((c - cOff[1]) / cSpan[1]) / (1 - PAD) + 1) / 2) * (by1 - by0);
    return [(px: number) => nxInv(xs.invert(px)), (py: number) => nyInv(ys.invert(py))];
  }

  // ---- marker drag (drag-to-rotate the star projection) ----
  let markerDrag: { kind: string; id: number } | null = null;
  function startMarkerDrag(e: PointerEvent, kind: string, id: number) {
    if (!draggableMarkers) return;
    e.stopPropagation();
    e.preventDefault();
    markerDrag = { kind, id };
    (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  }
  function moveMarkerDrag(e: PointerEvent) {
    if (!markerDrag || !container) return;
    const inv = invScales();
    if (!inv) return;
    const r = container.getBoundingClientRect();
    onmarkerdrag?.(markerDrag.kind, markerDrag.id, inv[0](e.clientX - r.left), inv[1](e.clientY - r.top));
  }
  function endMarkerDrag() { markerDrag = null; }

  function buildPoints(): number[][] {
    updateNormalizers();
    const minC = Math.min(...color), maxC = Math.max(...color);
    return points.map((p, i) => {
      const valueA = categorical
        ? (color[i] | 0)
        : color.length ? (color[i] - minC) / (maxC - minC || 1) : 0.5;
      return [nx(p[0]), ny(p[1]), valueA, 0];
    });
  }

  // Nudge overlapping cluster labels apart and keep them inside the plot, with
  // margins for the axis captions on the left/bottom borders.
  function declutter(items: { x: number; y: number; label: string; count: number }[]) {
    const charW = 7.6, padX = 40, hh = 11, gap = 6;
    const mLeft = 26, mBottom = 24, mEdge = 6;
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

  function scales(): [(v: number) => number, (v: number) => number] {
    const xs = scatter?.get("xScale") ?? xScale;
    const ys = scatter?.get("yScale") ?? yScale;
    return [(v: number) => xs(nx(v)), (v: number) => ys(ny(v))];
  }

  function positionOverlays() {
    if (!scatter) return;
    const [px, py] = scales();
    const xs = scatter.get("xScale") ?? xScale;
    const ys = scatter.get("yScale") ?? yScale;

    optPx = showOptimum && optimum ? [px(optimum[0]), py(optimum[1])] : null;
    clusterPx = showClusters
      ? declutter(clusters.map((c) => ({ x: px(c.x), y: py(c.y), label: c.label, count: c.count })))
      : [];
    pinPx = pins.map((p) => ({ x: px(p.point[0]), y: py(p.point[1]), letter: p.letter, color: p.color, id: p.id }));

    spokePx = showSpokes && optimum
      ? spokes.map((s) => ({
          x1: px(optimum[0]), y1: py(optimum[1]),
          x2: px(s.x), y2: py(s.y), label: s.label, key: s.key,
        }))
      : [];

    if (showCompass && compass && compass.length) {
      const maxN = Math.max(...compass.map((a) => Math.hypot(a.dx, a.dy))) || 1;
      const L = dataSpan * 0.34;
      arrowPx = compass.map((a) => {
        const ex = dataCentroid[0] + (a.dx / maxN) * L;
        const ey = dataCentroid[1] + (a.dy / maxN) * L;
        return { x1: px(dataCentroid[0]), y1: py(dataCentroid[1]), x2: px(ex), y2: py(ey), label: a.label };
      });
    } else arrowPx = [];

    if (path) {
      const x1 = px(path.a[0]), y1 = py(path.a[1]);
      const x2 = px(path.b[0]), y2 = py(path.b[1]);
      pathPx = { x1, y1, x2, y2, mx: x1 + (x2 - x1) * path.t, my: y1 + (y2 - y1) * path.t };
    } else pathPx = null;

    // contours are computed once in a fixed virtual grid; map grid->screen here
    const a = (xs(1) - xs(-1)) / CONTOUR_GRID;
    const d = (ys(1) - ys(-1)) / CONTOUR_GRID;
    contourTf = `matrix(${a},0,0,${d},${xs(-1)},${ys(-1)})`;
  }

  // density contours over the projected points ("option topography")
  function computeContours() {
    if (!showDensity || !points.length) { contourPaths = []; return; }
    updateNormalizers();
    const S = CONTOUR_GRID;
    const data: [number, number][] = points.map((p) => [
      ((nx(p[0]) + 1) / 2) * S,
      ((ny(p[1]) + 1) / 2) * S,
    ]);
    const gen = contourDensity<[number, number]>()
      .x((d) => d[0]).y((d) => d[1])
      .size([S, S]).bandwidth(14).thresholds(10);
    const cs = gen(data);
    contourPaths = cs.map((c, i) => ({
      d: c.coordinates
        .map((poly) =>
          poly.map((ring) => "M" + ring.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("L") + "Z").join(""),
        )
        .join(""),
      o: 0.12 + 0.55 * (i / Math.max(1, cs.length - 1)),
    }));
  }

  async function redraw() {
    if (!scatter) return;
    if (!points.length) {
      // e.g. star mode before the normalized samples arrive: show nothing
      // rather than the previous projection's stale points
      await scatter.clearPoints();
      positionOverlays();
      return;
    }
    await scatter.set({
      colorBy: "valueA",
      pointColor: categorical ? palette() : ramp(),
      pointColorActive: categorical ? palette() : ramp(),
      pointColorHover: categorical ? palette() : ramp(),
      zDataType: categorical ? "categorical" : "continuous",
    });
    await scatter.draw(buildPoints());
    positionOverlays();
  }

  export function reset() {
    scatter?.reset();
    applyCameraBounds(); // reset() re-runs initCamera, which can rebuild the camera
  }

  // ---- keep the camera over the data ----
  // Points are normalized into clip space [-1,1]·(1-PAD), so the cloud always
  // occupies a known box no matter what the projection is. Without bounds you can
  // pan the cloud off-screen and zoom out until it is a dot, with no way back
  // except "Reset view".
  //
  // regl-scatterplot builds its camera itself and never forwards these, so we
  // reach through `get("camera")` — the camera object does support them
  // (dom-2d-camera), it just isn't wired to the scatterplot's options.
  // scaleBounds: [min, max] zoom factor. 1 = the default framing, so 0.9 allows a
  // sliver of zoom-out for context and 60 is a deep zoom for dense regions.
  // translationBounds are in the same clip units as the points.
  const SCALE_BOUNDS: [number, number] = [0.9, 60];
  const PAN = 1.15; // a little slack past the cloud so edge points aren't pinned
  function applyCameraBounds() {
    const cam = scatter?.get("camera");
    if (!cam?.setScaleBounds) return;
    cam.setScaleBounds(SCALE_BOUNDS);
    // the cloud is centred on cOff now, so the pan box travels with it
    cam.setTranslationBounds([
      [-PAN + cOff[0], PAN + cOff[0]],
      [-PAN + cOff[1], PAN + cOff[1]],
    ]);
  }

  // ---- sampler-walk animation (comet trails along chain order) ----
  let walkRaf = 0;
  let walkPos = 0;
  const TRAIL = 70;

  function sizeWalkCanvas() {
    if (!walkCanvas || !container) return;
    const dpr = window.devicePixelRatio || 1;
    walkCanvas.width = container.clientWidth * dpr;
    walkCanvas.height = container.clientHeight * dpr;
    walkCanvas.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function walkFrame() {
    const ctx = walkCanvas?.getContext("2d");
    if (!ctx || !scatter) return;
    ctx.clearRect(0, 0, container.clientWidth, container.clientHeight);
    if (!walkActive || walkChains < 1 || !points.length) return;
    const [px, py] = scales();
    const P = palette();
    const len = Math.floor(points.length / walkChains);
    walkPos = (walkPos + 2) % len;
    for (let c = 0; c < walkChains; c++) {
      ctx.strokeStyle = P[c % P.length];
      const base = c * len;
      for (let k = TRAIL; k >= 1; k--) {
        const i1 = (walkPos - k + len * 4) % len;
        const i2 = (i1 + 1) % len;
        const p1 = points[base + i1], p2 = points[base + i2];
        if (!p1 || !p2) continue;
        ctx.globalAlpha = 0.55 * (1 - k / TRAIL);
        ctx.beginPath();
        ctx.moveTo(px(p1[0]), py(p1[1]));
        ctx.lineTo(px(p2[0]), py(p2[1]));
        ctx.stroke();
      }
      // comet head
      const h = points[base + ((walkPos % len) + len) % len];
      if (h) {
        ctx.globalAlpha = 0.95;
        ctx.fillStyle = P[c % P.length];
        ctx.beginPath();
        ctx.arc(px(h[0]), py(h[1]), 3.2, 0, 2 * Math.PI);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
    walkRaf = requestAnimationFrame(walkFrame);
  }

  onMount(() => {
    scatter = createScatterplot({
      canvas,
      width: "auto",
      height: "auto",
      pointSize: 3,
      pointSizeSelected: 2,
      opacity: 0.4,
      backgroundColor: [0, 0, 0, 0],
      xScale,
      yScale,
      lassoOnLongPress: false,
      opacityInactiveScale: 0.12,
      lassoColor: themeToken("--accent", "#2dd4bf"),
    });
    scatter.subscribe("pointOver", (i: number) => onhover?.(i));
    scatter.subscribe("pointOut", () => onhover?.(null));
    scatter.subscribe("view", positionOverlays);
    scatter.subscribe("select", ({ points: pts }: { points: number[] }) => {
      if (mode === "pan" && pts.length === 1) {
        // single click on a point in pan mode = pin it (not a selection)
        scatter.deselect({ preventEvent: true });
        onpin?.(pts[0]);
      } else {
        onselect?.(pts);
      }
    });
    scatter.subscribe("deselect", () => onselect?.([]));
    scatter.set({ mouseMode: mode === "select" ? "lasso" : "panZoom" });
    applyCameraBounds();
    sizeWalkCanvas();
    // contours are NOT computed here: redraw() sets up the nx/ny normalizers
    // asynchronously, and the data $effect schedules a (debounced) contour pass
    redraw();

    const ro = new ResizeObserver(() => {
      sizeWalkCanvas();
      // the normalizers are a function of the container size once `inset` is in
      // play (the visible window is a fraction of the canvas), so a resize has to
      // recompute them — repositioning the overlays alone would leave the points
      // mapped for the old width
      redraw();
      requestAnimationFrame(positionOverlays);
    });
    ro.observe(container);
    return () => {
      cancelAnimationFrame(walkRaf);
      ro.disconnect();
      scatter?.destroy();
    };
  });

  // data / encoding changes. Contours are debounced: during continuous point
  // updates (star-anchor drags, tours) KDE on 20k points per frame would stall.
  let contourTimer: ReturnType<typeof setTimeout> | null = null;
  $effect(() => {
    points; color; categorical; optimum; inset;
    if (!scatter) return;
    redraw();
    contourTimer = setTimeout(computeContours, 220);
    return () => { if (contourTimer) clearTimeout(contourTimer); };
  });
  // contour visibility
  $effect(() => {
    showDensity;
    if (scatter) computeContours();
  });
  // overlay visibility / content changes
  $effect(() => {
    showOptimum; showClusters; clusters; pins; spokes; showSpokes; compass; showCompass; path;
    if (scatter) positionOverlays();
  });
  // external selection (e.g. parallel-coords brush, kNN)
  $effect(() => {
    if (!scatter) return;
    if (selected && selected.length) scatter.select(selected, { preventEvent: true });
    else scatter.deselect({ preventEvent: true });
  });
  // theme flip: regl bakes the colour maps at set() time and the walk trail is a
  // canvas, so neither follows CSS — both have to be pushed again.
  $effect(() => {
    themeTick;
    if (!scatter) return;
    scatter.set({ lassoColor: themeToken("--accent", "#2dd4bf") });
    redraw();
  });

  // pan vs lasso mode
  $effect(() => {
    scatter?.set({ mouseMode: mode === "select" ? "lasso" : "panZoom" });
  });
  // sampler walk on/off
  $effect(() => {
    walkActive; walkChains;
    cancelAnimationFrame(walkRaf);
    if (walkActive && walkChains > 0) walkRaf = requestAnimationFrame(walkFrame);
    else walkCanvas?.getContext("2d")?.clearRect(0, 0, container?.clientWidth ?? 0, container?.clientHeight ?? 0);
  });
</script>

<div class="wrap" bind:this={container}>
  <canvas bind:this={canvas}></canvas>
  <canvas class="walk" bind:this={walkCanvas}></canvas>

  <svg class="overlay">
    <!-- option topography (density contours) -->
    {#if showDensity && contourPaths.length}
      <g transform={contourTf}>
        {#each contourPaths as c}
          <path d={c.d} class="contour" style="stroke-opacity:{c.o}" vector-effect="non-scaling-stroke" />
        {/each}
      </g>
    {/if}

    <!-- extreme-design spokes -->
    {#each spokePx as s}
      <line x1={s.x1} y1={s.y1} x2={s.x2} y2={s.y2} class="spoke-line" />
      <circle
        cx={s.x2} cy={s.y2} r="5" class="spoke-dot"
        role="button" tabindex="-1" aria-label="pin {s.label}"
        onclick={() => onspoke?.(s.key)}
        onkeydown={(e) => e.key === "Enter" && onspoke?.(s.key)}
      >
        <title>{s.label} — click to pin</title>
      </circle>
    {/each}

    <!-- tech compass (PCA loading arrows) -->
    {#each arrowPx as a}
      {@const ang = Math.atan2(a.y2 - a.y1, a.x2 - a.x1)}
      <line x1={a.x1} y1={a.y1} x2={a.x2} y2={a.y2} class="arrow" />
      <line x1={a.x2} y1={a.y2} x2={a.x2 - 7 * Math.cos(ang - 0.4)} y2={a.y2 - 7 * Math.sin(ang - 0.4)} class="arrow" />
      <line x1={a.x2} y1={a.y2} x2={a.x2 - 7 * Math.cos(ang + 0.4)} y2={a.y2 - 7 * Math.sin(ang + 0.4)} class="arrow" />
      <text
        x={a.x2 + 10 * Math.cos(ang)} y={a.y2 + 10 * Math.sin(ang)}
        class="arrow-label" text-anchor={Math.cos(ang) < -0.3 ? "end" : Math.cos(ang) > 0.3 ? "start" : "middle"}
      >{a.label}</text>
    {/each}

    <!-- A→B design path -->
    {#if pathPx}
      <line x1={pathPx.x1} y1={pathPx.y1} x2={pathPx.x2} y2={pathPx.y2} class="path-line" />
      <circle cx={pathPx.mx} cy={pathPx.my} r="6" class="path-marker" />
    {/if}
  </svg>

  {#each clusterPx as c}
    <div class="cluster" style="left:{c.x}px; top:{c.y}px">
      <span class="dot"></span>
      <span class="clabel">{c.label}</span>
    </div>
  {/each}

  {#each pinPx as p}
    <div
      class="pin" class:draggable={draggableMarkers}
      style="left:{p.x}px; top:{p.y}px; --pc:{p.color}"
      onpointerdown={(e) => startMarkerDrag(e, "pin", p.id)}
      onpointermove={moveMarkerDrag}
      onpointerup={endMarkerDrag}
      title={draggableMarkers ? "drag to rotate the projection" : ""}
    >{p.letter}</div>
  {/each}

  {#if optPx}
    <div
      class="optimum" class:draggable={draggableMarkers}
      style="left:{optPx[0]}px; top:{optPx[1]}px"
      onpointerdown={(e) => startMarkerDrag(e, "optimum", -1)}
      onpointermove={moveMarkerDrag}
      onpointerup={endMarkerDrag}
      title={draggableMarkers ? "drag to rotate the projection" : "Cost optimum"}
    ></div>
  {/if}
</div>

<style>
  .wrap {
    position: relative;
    width: 100%;
    height: 100%;
  }
  canvas { width: 100%; height: 100%; display: block; }
  .walk { position: absolute; inset: 0; pointer-events: none; }

  .overlay {
    position: absolute; inset: 0; width: 100%; height: 100%;
    pointer-events: none; overflow: visible;
  }
  .contour { fill: none; stroke: var(--accent); stroke-width: 1; }
  .spoke-line { stroke: var(--b-20); stroke-dasharray: 3 4; }
  .spoke-dot {
    fill: var(--marker-fill); stroke: var(--accent); stroke-width: 1.6;
    pointer-events: all; cursor: pointer;
  }
  .spoke-dot:hover { fill: var(--accent); }
  .arrow { stroke: var(--extreme); stroke-width: 1.4; stroke-opacity: 0.85; }
  .arrow-label {
    fill: var(--extreme); font-size: 11px; font-weight: 600;
    paint-order: stroke; stroke: var(--halo); stroke-width: 3px;
  }
  .path-line {
    filter: drop-shadow(0 0 4px var(--tick));
    stroke: var(--tick); stroke-width: 1.6; stroke-dasharray: 6 5;
  }
  .path-marker {
    filter: drop-shadow(0 0 6px var(--tick));
    fill: var(--tick); stroke: var(--halo); stroke-width: 1.5;
  }

  .cluster {
    position: absolute; transform: translate(-50%, -50%);
    display: flex; align-items: center; gap: 5px;
    pointer-events: none; white-space: nowrap;
  }
  .cluster .dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--tick);
    box-shadow: 0 0 0 2px var(--halo), 0 0 8px color-mix(in srgb, var(--accent) 60%, transparent);
    flex: none;
  }
  /* Same readability problem as the hover tooltip: --panel-glass is 72/88%, and
     a dense point cloud read straight through the label sitting on it. Solid
     panel + blur, so the words win over the data behind them. */
  .cluster .clabel {
    font-size: 11px; font-weight: 600; color: var(--fg);
    background: var(--panel-solid);
    backdrop-filter: blur(3px);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    border-radius: 6px; padding: 1px 6px;
  }

  .pin {
    position: absolute; transform: translate(-50%, -50%);
    width: 19px; height: 19px; border-radius: 50%;
    background: var(--pc);
    color: var(--on-accent); font-size: 11px; font-weight: 800; line-height: 19px;
    text-align: center;
    box-shadow: 0 0 0 2px var(--halo), 0 0 10px var(--pc);
    pointer-events: none;
  }

  .optimum {
    position: absolute; width: 16px; height: 16px; margin: -8px 0 0 -8px;
    border: 2.5px solid var(--tick); border-radius: 50%;
    box-shadow: 0 0 0 1.5px var(--halo), 0 0 6px var(--halo);
    pointer-events: none;
  }
  .optimum::after {
    content: ""; position: absolute; inset: 5px;
    background: var(--tick); border-radius: 50%;
  }
  .pin.draggable, .optimum.draggable {
    pointer-events: auto; cursor: grab; touch-action: none;
  }
  .pin.draggable:active, .optimum.draggable:active { cursor: grabbing; }
</style>
