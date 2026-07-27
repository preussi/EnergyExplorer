<script lang="ts">
  import { onMount } from "svelte";
  import { getDatasets, buildPreloaded, buildUpload, type Meta, type PreloadedDataset } from "./api";

  // Entry screen: introduce the tool, let the user pick a preloaded polytope (or
  // upload their own), choose how many samples to generate, then kick off the
  // server-side build (sampling + precompute of every view) and hand back meta.
  let { onready }: { onready: (meta: Meta) => void } = $props();

  const UPLOAD = "__upload__";
  let datasets = $state<PreloadedDataset[]>([]);
  let sel = $state<string | null>(null);        // dataset id, or UPLOAD
  let uploadFiles = $state<FileList | null>(null);
  let nSamples = $state(20000);
  let busy = $state(false);
  let err = $state<string | null>(null);

  onMount(() => {
    getDatasets()
      .then((r) => { datasets = r.datasets; if (datasets.length) sel = datasets[0].id; })
      .catch((e) => (err = (e as Error).message));
  });

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
      <div class="label">1 · Choose a design space</div>
      <div class="datasets">
        {#each datasets as d}
          <button class="ds" class:active={sel === d.id} onclick={() => (sel = d.id)} disabled={busy}>
            <span class="ds-name">{d.name}</span>
            <span class="ds-sub">{d.n_axes} technologies · preloaded</span>
          </button>
        {/each}
        <button class="ds" class:active={sel === UPLOAD} onclick={() => (sel = UPLOAD)} disabled={busy}>
          <span class="ds-name">Upload your own polytope</span>
          <span class="ds-sub">.npz — technologies + cost, shipped schema</span>
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

    {#if err}<div class="err">⚠ {err}</div>{/if}
  </div>
</div>

<style>
  .landing {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    padding: 24px; overflow-y: auto;
    background:
      radial-gradient(1200px 600px at 50% -10%, rgba(45, 212, 191, 0.10), transparent 60%),
      #0a0e14;
  }
  .card {
    width: min(640px, 100%); display: flex; flex-direction: column; gap: 22px;
    background: rgba(20, 27, 38, 0.72); border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 18px; padding: 30px 32px; backdrop-filter: blur(8px);
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
  }
  h1 { margin: 0 0 10px; font-size: 26px; letter-spacing: -0.01em; color: #eef4f2; }
  .lede { margin: 0 0 8px; font-size: 14px; line-height: 1.55; color: var(--fg); }
  .how { margin: 0; font-size: 12.5px; line-height: 1.5; color: var(--muted); }
  .lede strong { color: var(--accent); }

  .label {
    font-size: 10px; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--muted); margin-bottom: 10px;
  }
  .datasets { display: flex; flex-direction: column; gap: 8px; }
  .ds {
    display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
    text-align: left; padding: 10px 13px; border-radius: 11px; cursor: pointer;
    background: rgba(255, 255, 255, 0.025); border: 1px solid rgba(255, 255, 255, 0.08);
  }
  .ds:hover:not(:disabled) { background: rgba(255, 255, 255, 0.05); }
  .ds.active { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
  .ds-name { font-size: 13.5px; color: #eef4f2; }
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
    background: rgba(255, 255, 255, 0.05); color: var(--fg);
    border: 1px solid rgba(255, 255, 255, 0.12);
  }
  .note { display: block; margin-top: 7px; font-size: 11px; color: var(--muted); }
  .note.warn { color: #f0a14e; }

  .run {
    margin-top: 4px; padding: 14px; border-radius: 12px; cursor: pointer;
    font-size: 15px; font-weight: 700; letter-spacing: 0.01em;
    background: var(--accent); color: #06241f; border: none;
    transition: filter 0.15s, opacity 0.15s;
  }
  .run:hover:not(:disabled) { filter: brightness(1.08); }
  .run:disabled { opacity: 0.5; cursor: default; }

  .err {
    font-size: 12px; color: #f0a14e; background: rgba(240, 161, 78, 0.08);
    border: 1px solid rgba(240, 161, 78, 0.3); border-radius: 8px; padding: 8px 12px;
  }
</style>
