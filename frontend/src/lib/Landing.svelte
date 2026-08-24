<script lang="ts">
  import { onMount } from "svelte";
  import { getDatasets, buildPreloaded, buildUpload, type Meta, type PreloadedDataset } from "./api";

  // Entry screen: introduce the tool, let the user pick a preloaded polytope (or
  // upload their own), choose how many samples to generate, then kick off the
  // server-side build (sampling + precompute of every view) and hand back meta.
  // `oncancel` is only passed when there is a live session to go back to — the
  // landing page is otherwise a dead end, and "change dataset" used to be a
  // one-way door: it dropped the session id, so there was no way back to the
  // cloud you were just looking at without rebuilding it.
  let { onready, oncancel = null, currentName = null }:
    { onready: (meta: Meta) => void;
      oncancel?: (() => void) | null;
      currentName?: string | null } = $props();

  const UPLOAD = "__upload__";
  let datasets = $state<PreloadedDataset[]>([]);
  let sel = $state<string | null>(null);        // dataset id, or UPLOAD
  let uploadFiles = $state<FileList | null>(null);
  let nSamples = $state(20000);
  let busy = $state(false);
  let err = $state<string | null>(null);

  // axis limits, so a slow build is predicted here rather than discovered
  let maxAxes = $state(24);
  let warnAxes = $state(16);
  onMount(() => {
    getDatasets()
      .then((r) => {
        datasets = r.datasets;
        if (r.max_axes) maxAxes = r.max_axes;
        if (r.warn_axes) warnAxes = r.warn_axes;
        if (datasets.length) sel = datasets[0].id;
      })
      .catch((e) => (err = (e as Error).message));
  });
  // the facet sweep is the quadratic term of a build: C(n,2) shadows at ~70 ms
  const selAxes = $derived(datasets.find((d) => d.id === sel)?.n_axes ?? 0);
  const slowBuild = $derived(selAxes > warnAxes
    ? Math.round((selAxes * (selAxes - 1) / 2) * 0.071 * (selAxes / 9))
    : 0);

  const nOk = $derived(Number.isFinite(nSamples) && nSamples >= 1000 && nSamples <= 100000);
  const canRun = $derived(
    !busy && nOk && (sel === UPLOAD ? !!uploadFiles?.length : !!sel),
  );

  async function run() {
    if (!canRun || sel === null) return;
    busy = true; err = null;
    try {
      const meta = sel === UPLOAD
        ? await buildUpload(uploadFiles![0], nSamples)
        : await buildPreloaded(sel, nSamples);
      onready(meta);
    } catch (e) {
      err = (e as Error).message;
      busy = false;
    }
  }
</script>

<div class="landing">
  <div class="card">
    <header>
      <h1>Energy&nbsp;Explorer</h1>
      <p class="lede">
        Explore the space of <strong>near-optimal energy-system designs</strong> — every
        technology mix whose cost stays within a few percent of the optimum. Instead of one
        “optimal” answer, see, filter, and steer the whole space of viable designs.
      </p>
      <p class="how">
        Pick a polytope below → we generate a uniform cloud of near-optimal designs inside it →
        you explore their couplings, trade-off facets, and flexibility.
      </p>
    </header>

    <section>
      {#if slowBuild}
        <div class="slow-note">
          {selAxes} technologies means {selAxes * (selAxes - 1) / 2} facet pairs to
          precompute — expect roughly {slowBuild}s before the tool opens.
        </div>
      {/if}
      <div class="label">1 · Choose a design space</div>
      <div class="datasets">
        {#each datasets as d}
          <button class="ds" class:active={sel === d.id} onclick={() => (sel = d.id)} disabled={busy}>
            <span class="ds-name">{d.name}</span>
            <span class="ds-sub">
              {d.n_axes} technologies · preloaded{#if d.n_axes > warnAxes} · slow build{/if}
            </span>
          </button>
        {/each}
        <button class="ds" class:active={sel === UPLOAD} onclick={() => (sel = UPLOAD)} disabled={busy}>
          <span class="ds-name">Upload your own polytope</span>
          <span class="ds-sub">.npz — technologies + cost, shipped schema · up to {maxAxes} technologies</span>
        </button>
      </div>
      {#if sel === UPLOAD}
        <label class="file">
          polytope&nbsp;.npz
          <input type="file" accept=".npz" bind:files={uploadFiles} disabled={busy} />
        </label>
      {/if}
    </section>

    <section>
      <div class="label">2 · How many designs to generate</div>
      <div class="samples">
        <input class="slider" type="range" min="1000" max="100000" step="1000"
               bind:value={nSamples} disabled={busy} />
        <input class="num" type="number" min="1000" max="100000" step="1000"
               bind:value={nSamples} disabled={busy} />
      </div>
      <span class="note" class:warn={!nOk}>
        1,000 – 100,000 · more samples give denser views but take longer to generate
      </span>
    </section>

    <button class="run" onclick={run} disabled={!canRun}>
      {busy ? `Generating ${nSamples.toLocaleString()} designs & precomputing views…` : "Generate & Explore →"}
    </button>

    {#if oncancel}
      <button class="back" onclick={oncancel} disabled={busy}>
        ← keep exploring {currentName ?? "your current dataset"}
      </button>
    {/if}

    {#if err}<div class="err">⚠ {err}</div>{/if}
  </div>
</div>

<style>
  .landing {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    padding: 24px; overflow-y: auto;
    /* flat, like the rest of the app — this used to carry an accent-tinted
       radial wash over --on-accent */
    background: var(--page);
  }
  .card {
    width: min(640px, 100%); display: flex; flex-direction: column; gap: 22px;
    background: var(--panel-glass); border: 1px solid var(--b-08);
    border-radius: 18px; padding: 30px 32px; backdrop-filter: blur(8px);
    box-shadow: var(--shadow-lg);
  }
  h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: -0.01em; color: var(--fg); }
  .lede { margin: 0 0 8px; font-size: 14px; line-height: 1.55; color: var(--fg); }
  .how { margin: 0; font-size: 12.5px; line-height: 1.5; color: var(--muted); }
  .lede strong { color: var(--accent); }

  .slow-note {
    font-size: 11.5px; line-height: 1.4; color: var(--amber);
    background: color-mix(in srgb, var(--amber) 10%, transparent); border: 1px solid color-mix(in srgb, var(--amber) 35%, transparent);
    border-radius: 8px; padding: 7px 10px; margin-bottom: 12px;
  }
  .label {
    font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 10px;
  }
  .datasets { display: flex; flex-direction: column; gap: 8px; }
  .ds {
    display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
    text-align: left; padding: 10px 13px; border-radius: 11px; cursor: pointer;
    background: var(--s-02); border: 1px solid var(--b-08);
  }
  .ds:hover:not(:disabled) { background: var(--s-05); }
  .ds.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .ds-name { font-size: 13.5px; color: var(--fg); }
  .ds-sub { font-size: 11px; color: var(--muted); }

  .file {
    display: flex; flex-direction: column; gap: 5px; margin-top: 10px;
    font-size: 11px; color: var(--muted);
  }
  .file input { color: var(--fg); }

  .samples { display: flex; align-items: center; gap: 12px; }
  .slider { flex: 1; accent-color: var(--accent); }
  .num {
    width: 96px; font-size: 13px; padding: 5px 8px; border-radius: 7px;
    background: var(--s-05); color: var(--fg);
    border: 1px solid var(--b-12);
  }
  .note { display: block; margin-top: 7px; font-size: 11px; color: var(--muted); }
  .note.warn { color: var(--warn); }

  .run {
    margin-top: 4px; padding: 14px; border-radius: 12px; cursor: pointer;
    font-size: 15px; font-weight: 700; letter-spacing: 0.01em;
    background: var(--accent); color: var(--on-accent); border: none;
    transition: filter 0.15s, opacity 0.15s;
  }
  .run:hover:not(:disabled) { filter: brightness(1.08); }
  .run:disabled { opacity: 0.5; cursor: default; }

  .back {
    align-self: center; background: none; border: none; padding: 2px 4px;
    font-size: 12px; color: var(--muted); cursor: pointer; text-decoration: underline;
    max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .back:hover:not(:disabled) { color: var(--accent); }
  .back:disabled { opacity: 0.5; cursor: default; }
  .err {
    font-size: 12px; color: var(--warn); background: color-mix(in srgb, var(--warn) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent); border-radius: 8px; padding: 8px 12px;
  }
</style>
