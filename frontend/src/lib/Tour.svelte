<script lang="ts">
  import { onMount } from "svelte";

  // Guided tour: dims the page, cuts a hole around the element a step is talking
  // about, and puts a card next to it. The hole is a real gap between four dim
  // panels rather than an SVG mask, so the highlighted control stays clickable —
  // the point is to teach the buttons, not to lock the user out of them.
  export interface TourStep {
    /** CSS selector of the element to spotlight; omit for a centred card */
    target?: string;
    title: string;
    /** short paragraphs */
    body: string[];
    /** label override for the forward button */
    next?: string;
    /** opaque tag the host uses to set the app up for this step; unused here */
    setup?: string;
  }

  let {
    steps = [],
    step = 0,
    onstep,
    onclose,
  }: {
    steps: TourStep[];
    step: number;
    onstep: (i: number) => void;
    onclose: () => void;
  } = $props();

  const cur = $derived(steps[step]);
  const last = $derived(step >= steps.length - 1);

  // Where the spotlight is. Re-measured whenever the step changes and on every
  // resize; also polled for a few frames because panels slide/expand as a step's
  // action runs, and a stale rect points at nothing.
  let rect = $state<{ top: number; left: number; width: number; height: number } | null>(null);
  let vw = $state(0), vh = $state(0);
  const PAD = 8;

  function measure() {
    vw = window.innerWidth; vh = window.innerHeight;
    const sel = cur?.target;
    if (!sel) { rect = null; return; }
    const el = document.querySelector(sel);
    if (!el) { rect = null; return; }
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) { rect = null; return; }
    rect = {
      top: Math.max(0, r.top - PAD), left: Math.max(0, r.left - PAD),
      width: r.width + PAD * 2, height: r.height + PAD * 2,
    };
  }

  // settle: the target may still be animating in when the step opens
  $effect(() => {
    step;                              // re-run on every step change
    let frames = 0;
    let raf = 0;
    const tick = () => {
      measure();
      if (++frames < 30) raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  });

  onMount(() => {
    const on = () => measure();
    window.addEventListener("resize", on);
    window.addEventListener("scroll", on, true);
    return () => {
      window.removeEventListener("resize", on);
      window.removeEventListener("scroll", on, true);
    };
  });

  function key(e: KeyboardEvent) {
    if (e.key === "Escape") onclose();
    else if (e.key === "ArrowRight" || e.key === "Enter") next();
    else if (e.key === "ArrowLeft") back();
  }
  const next = () => (last ? onclose() : onstep(step + 1));
  const back = () => step > 0 && onstep(step - 1);

  // Card placement: prefer the side of the target with the most room, so the card
  // never covers the thing it is pointing at.
  //
  // The card's height is MEASURED, not assumed. It used to be hardcoded at 260px,
  // which pushed the taller steps (three paragraphs) off the bottom of the window
  // and took the Back/Next buttons with them — the step became a dead end.
  const CARD_W = 330;
  let cardH = $state(260);
  const card = $derived.by(() => {
    if (!rect) return null;
    const gap = 14;
    const h = cardH || 260;
    // the whole card must stay on screen — the footer is the only way forward
    const clampTop = (t: number) => Math.max(8, Math.min(t, vh - h - 8));
    const clampLeft = (l: number) => Math.max(8, Math.min(l, vw - CARD_W - 8));
    const right = vw - (rect.left + rect.width);
    const below = vh - (rect.top + rect.height);
    if (right > CARD_W + gap)
      return { left: rect.left + rect.width + gap, top: clampTop(rect.top), side: "right" };
    if (rect.left > CARD_W + gap)
      return { left: rect.left - CARD_W - gap, top: clampTop(rect.top), side: "left" };
    if (below > h + gap)
      return { left: clampLeft(rect.left), top: rect.top + rect.height + gap, side: "below" };
    return { left: clampLeft(rect.left), top: clampTop(rect.top - h - gap), side: "above" };
  });
</script>

<svelte:window onkeydown={key} />

{#if cur}
  <!-- dimmed surround; the gap over `rect` stays interactive -->
  {#if rect}
    <div class="dim" style="top:0; left:0; width:100vw; height:{rect.top}px"></div>
    <div class="dim" style="top:{rect.top + rect.height}px; left:0; width:100vw; height:{Math.max(0, vh - rect.top - rect.height)}px"></div>
    <div class="dim" style="top:{rect.top}px; left:0; width:{rect.left}px; height:{rect.height}px"></div>
    <div class="dim" style="top:{rect.top}px; left:{rect.left + rect.width}px; width:{Math.max(0, vw - rect.left - rect.width)}px; height:{rect.height}px"></div>
    <div class="ring" style="top:{rect.top}px; left:{rect.left}px; width:{rect.width}px; height:{rect.height}px"></div>
  {:else}
    <div class="dim" style="inset:0; width:100vw; height:100vh"></div>
  {/if}

  <div class="card" class:centred={!rect} bind:clientHeight={cardH}
       style={rect && card ? `top:${card.top}px; left:${card.left}px` : ""}>
    <div class="head">
      <span class="count">{step + 1} / {steps.length}</span>
      <button class="x" onclick={onclose} aria-label="close the guide" title="close (Esc)">✕</button>
    </div>
    <!-- only the prose scrolls; the footer stays pinned so Next is always
         reachable even on a short window -->
    <div class="body">
      <h2>{cur.title}</h2>
      {#each cur.body as p}<p>{p}</p>{/each}
    </div>
    <div class="foot">
      <div class="dots">
        {#each steps as _s, i}
          <button class="dot" class:on={i === step} aria-label={`step ${i + 1}`}
                  onclick={() => onstep(i)}></button>
        {/each}
      </div>
      <div class="btns">
        {#if step > 0}<button class="ghost" onclick={back}>Back</button>{/if}
        <button class="go" onclick={next}>{cur.next ?? (last ? "Done" : "Next")}</button>
      </div>
    </div>
  </div>
{/if}

<style>
  .dim {
    backdrop-filter: blur(1.5px);
    position: fixed; z-index: 60;
    background: var(--panel-glass);
  }
  .ring {
    position: fixed; z-index: 61; pointer-events: none;
    border: 2px solid var(--accent); border-radius: 12px;
    box-shadow: 0 0 0 1px var(--halo), 0 0 22px color-mix(in srgb, var(--accent) 55%, transparent);
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 1px var(--halo), 0 0 12px color-mix(in srgb, var(--accent) 35%, transparent); }
    50%      { box-shadow: 0 0 0 1px var(--halo), 0 0 26px color-mix(in srgb, var(--accent) 75%, transparent); }
  }

  .card {
    position: fixed; z-index: 62; width: 330px;
    display: flex; flex-direction: column;
    max-height: calc(100vh - 16px);
    background: var(--panel-solid);
    box-shadow: var(--shadow-lg);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
    border-radius: 14px; padding: 14px 16px 12px;
  }
  .head, .foot { flex: none; }
  .body { min-height: 0; overflow-y: auto; }
  .card.centred { top: 50%; left: 50%; transform: translate(-50%, -50%); width: 420px; }

  .head { display: flex; align-items: center; justify-content: space-between; }
  .count { font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--muted); }
  .x {
    width: 22px; height: 22px; border-radius: 6px; padding: 0; line-height: 1; font-size: 12px;
    background: var(--s-05); border: 1px solid var(--b-12);
    color: var(--muted); cursor: pointer;
  }
  .x:hover { color: var(--fg); background: var(--s-09); }

  h2 { font-size: 15px; margin: 6px 0 8px; color: var(--fg); }
  p { font-size: 12.5px; line-height: 1.5; color: var(--fg); margin: 0 0 8px; }

  .foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 10px; }
  .dots { display: flex; gap: 5px; }
  .dot {
    width: 7px; height: 7px; padding: 0; border-radius: 50%; cursor: pointer;
    background: var(--b-20); border: none;
  }
  .dot.on { background: var(--accent); }
  .btns { display: flex; gap: 6px; }
  .ghost, .go {
    font-size: 12px; padding: 5px 12px; border-radius: 8px; cursor: pointer;
    border: 1px solid var(--b-12); background: var(--s-05); color: var(--fg);
  }
  .ghost:hover { background: var(--s-09); }
  .go { background: var(--accent); border-color: var(--accent); color: var(--on-accent); font-weight: 600; }
  .go:hover { filter: brightness(1.08); }
</style>
