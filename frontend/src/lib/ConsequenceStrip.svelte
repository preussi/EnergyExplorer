<script lang="ts">
  // Constraint → consequence readout for the Profiles rail, in two parts that sit
  // at opposite ends of the panel:
  //
  //   variant="headline"  the verdict — does anything survive at all, and is the
  //                       cloud dense enough here to show it. Belongs at the TOP,
  //                       above the rows it describes.
  //   variant="limits"    the per-lever consequences in words, at the bottom.
  //
  // There used to be a "N% of the space left" number here (a volume ratio,
  // measured two different ways depending on whether axes were hidden). It was
  // removed deliberately: the two worldviews disagreed in both directions, the
  // subset one was not monotone under hiding an axis, and a single headline
  // percentage read as a far more solid claim than a Monte-Carlo ratio over a
  // 9-D polytope actually is. What remains here is only what is exact (the LP
  // feasibility verdict) or plainly countable (how many sampled designs land in
  // the region, which is a statement about the CLOUD, not about the space).
  //
  // Both render unconditionally (with a placeholder when idle). A block that
  // appeared with the first constraint would change the panel's height, which
  // resizes the parallel-coords canvas and rebuilds every axis scale mid-drag.
  export type Consequence = {
    axis: string; kind: "force" | "include" | "limit"; text: string; mag: number;
  };
  /** How many of the sampled designs satisfy the current constraints. Used only
   *  to decide whether the cloud can resolve the region — NOT as a volume. */
  export type InRegion = { k: number; n: number };

  let {
    variant = "headline",
    consequences = null,
    inRegion = null,
    flexBusy = false,
    resampling = false,
    error = null,
    inCandidates = false,
    trustK = 200,
    selectedCount = null,
    onresample,
    onclearselection,
  }: {
    variant?: "headline" | "limits";
    consequences?: { infeasible: boolean; items: Consequence[] } | null;
    inRegion?: InRegion | null;
    flexBusy?: boolean;
    /** true while a region resample is in flight */
    resampling?: boolean;
    /** message from a failed resample, shown in place of the prompt */
    error?: string | null;
    /** the plot is showing designs generated inside the region, not the base cloud */
    inCandidates?: boolean;
    /** below this many in-region samples the cloud can't resolve the region */
    trustK?: number;
    /** rows currently selected — shown here rather than in a row of its own,
     *  which used to appear and disappear and resize the plot below it */
    selectedCount?: number | null;
    onresample?: () => void;
    onclearselection?: () => void;
  } = $props();

  const underSampled = $derived(
    !!inRegion && inRegion.k < trustK && !consequences?.infeasible
    && !inCandidates);
</script>

{#if variant === "headline"}
  <div class="vol-head" class:busy={flexBusy}>
    <div class="vol-line">
      {#if !consequences}
        <span class="muted">whole space · drag along a row to narrow it</span>
      {:else if consequences.infeasible}
        <span class="cons-bad">✕ no feasible design under these constraints</span>
      {:else}
        <span class="ok" title="decided by a linear program on the polytope, not by whether samples happened to land in the region">✓ feasible designs remain</span>
      {/if}
      {#if selectedCount != null}
        <span class="sel">· {selectedCount.toLocaleString()} selected
          <button class="link" onclick={onclearselection}>clear</button>
        </span>
      {/if}
    </div>

    {#if error}
      <div class="note bad" title={error}>⚠ {error}</div>
    {:else if inCandidates}
      <div class="note muted" title="these designs were all generated inside the region, so they are not a uniform sample of the whole space">
        showing designs generated inside this region
      </div>
    {:else if underSampled}
      <button class="resample" onclick={onresample} disabled={resampling}
              title="run a fresh hit-and-run inside the constrained region — the base cloud is too sparse here to show its shape">
        {resampling ? "sampling…"
          : `only ${inRegion!.k.toLocaleString()} of ${inRegion!.n.toLocaleString()} designs land here — resample`}
      </button>
    {/if}
  </div>
{:else}
  <div class="limits" class:busy={flexBusy}>
    {#if consequences && !consequences.infeasible && consequences.items.length}
      <div class="lim-lead">this forces</div>
      {#each consequences.items as it}
        <div class="lim {it.kind}">{it.text}</div>
      {/each}
    {:else if consequences && !consequences.infeasible}
      <div class="muted">nothing is forced — this costs options, not decisions</div>
    {/if}
    <!-- deliberately blank when nothing is constrained: the headline at the top of
         the rail already says "whole space · drag along a row to narrow it", and
         two placeholders saying the same thing read as clutter. The slot keeps its
         fixed height either way — see .lim-slot. -->
  </div>
{/if}

<style>
  .vol-head, .limits { flex: none; transition: opacity 0.15s; }
  .vol-head.busy, .limits.busy { opacity: 0.6; }
  .muted { color: var(--muted); }

  .vol-line { display: flex; align-items: baseline; gap: 6px; font-size: 11px; }
  .ok { color: var(--muted); font-size: 11px; }
  .cons-bad { color: var(--warn); font-weight: 600; font-size: 12px; }
  .note { font-size: 10.5px; margin-top: 3px; line-height: 1.35; }
  .note.bad { color: var(--warn); }
  .sel { color: var(--muted); }
  .link { background: none; border: none; color: var(--accent); cursor: pointer; padding: 0 0 0 3px; text-decoration: underline; font-size: 10.5px; }
  .resample {
    display: block; margin-top: 4px; text-align: left; padding: 2px 9px;
    border-radius: 999px; font-size: 10.5px; line-height: 1.35;
    background: color-mix(in srgb, var(--accent) 12%, transparent); border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
    color: var(--accent); cursor: pointer;
  }
  .resample:disabled { opacity: 0.6; cursor: default; }

  /* wrapped sentences, not pills — in a rail the pills truncated */
  .limits { display: flex; flex-direction: column; gap: 2px; font-size: 10.5px; }
  .lim-lead {
    font-size: 9px; letter-spacing: 0.1em; text-transform: uppercase; color: var(--muted);
  }
  .lim { color: var(--fg); line-height: 1.35; padding-left: 7px; border-left: 2px solid var(--muted); }
  .lim.force, .lim.include { border-left-color: var(--amber); }
  .lim.limit { border-left-color: var(--limit); }
</style>
