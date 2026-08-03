/**
 * Proximity ordering of axes by hierarchical clustering.
 *
 * Same construction as Amumo's "cluster matrix by similarity"
 * (github.com/ginihumer/Amumo, `utils.get_cluster_sorting`): agglomerative
 * **complete-linkage** clustering over `1 - similarity`, then read the axes off
 * the dendrogram. Mutually-coupled axes land next to each other, so a coupled
 * group shows up as a block instead of scattered cells (matrix) / so trade-offs
 * read between neighbouring axes (parallel coords).
 *
 * Two deliberate departures from Amumo: (1) it cuts the tree with
 * `fcluster(..., 10, "maxclust")` and sorts by cluster id, which for 9 axes puts
 * every axis in its own cluster and degenerates to the input order — we take the
 * leaf order instead, which is what actually does the grouping; (2) a dendrogram
 * leaves each subtree's flip arbitrary (2^(k-1) equally valid readings), which is
 * very visible at this size, so we pick the flip set that minimizes the total
 * distance between neighbouring axes — Bar-Joseph optimal leaf ordering.
 *
 * Verified against `scipy.cluster.hierarchy`: identical merge sequence to
 * `linkage(..., method="complete")`, every agglomerated cluster stays a
 * contiguous block of the result, and the order lands within ~3% of the best of
 * all 9! permutations.
 *
 * @param S symmetric coupling matrix; higher = more coupled. Values need not be
 *          bounded (MI isn't) — they are rescaled by the strongest observed pair.
 * @returns a permutation of `0..n-1`
 */
export function clusterOrder(S: number[][]): number[] {
  const n = S.length;
  if (n < 3) return Array.from({ length: n }, (_, i) => i);
  // coupling → distance, scaled so the strongest observed pair sits at 0.
  // (MI is unbounded, so an absolute `1 - v` would not be comparable.)
  let mx = 0;
  for (let i = 0; i < n; i++)
    for (let j = 0; j < n; j++)
      if (i !== j) mx = Math.max(mx, S[i][j]);
  const D = S.map((row, i) => row.map((v, j) => (i === j ? 0 : 1 - v / (mx || 1))));

  // ---- complete-linkage agglomeration, recording the merge tree ----
  const kids = new Map<number, [number, number]>();
  const leaves = new Map<number, number[]>(Array.from({ length: n }, (_, i) => [i, [i]]));
  const live = new Set<number>(Array.from({ length: n }, (_, i) => i));
  for (let next = n; live.size > 1; next++) {
    let bi = -1, bj = -1, best = Infinity;
    for (const i of live)
      for (const j of live) {
        if (j <= i) continue;
        // complete linkage: cluster distance = the *worst* pair across the two
        let d = -Infinity;
        for (const p of leaves.get(i)!) for (const q of leaves.get(j)!) d = Math.max(d, D[p][q]);
        if (d < best) { best = d; bi = i; bj = j; }
      }
    kids.set(next, [bi, bj]);
    leaves.set(next, [...leaves.get(bi)!, ...leaves.get(bj)!]);
    live.delete(bi); live.delete(bj); live.add(next);
  }

  // ---- optimal leaf ordering ----
  // Per subtree, keep only the cheapest arrangement for each (first, last) leaf
  // pair: joining two subtrees costs one D lookup between the touching endpoints,
  // so nothing deeper than the endpoints can matter. That collapses the 2^(k-1)
  // flip combinations into at most k² states per node.
  type Arr = { order: number[]; cost: number };
  function arrange(node: number): Arr[] {
    const bestBy = new Map<string, Arr>();
    const put = (a: Arr) => {
      const k = `${a.order[0]}|${a.order[a.order.length - 1]}`;
      const prev = bestBy.get(k);
      if (!prev || a.cost < prev.cost) bestBy.set(k, a);
    };
    const kid = kids.get(node);
    if (!kid) { put({ order: [node], cost: 0 }); return [...bestBy.values()]; }
    // both flips of each side; the mirror (B before A) is the reverse of one of
    // these, same cost, and the parent tries reversals anyway
    const flips = (c: Arr) => [c, { order: [...c.order].reverse(), cost: c.cost }];
    for (const a0 of arrange(kid[0]))
      for (const b0 of arrange(kid[1]))
        for (const a of flips(a0))
          for (const b of flips(b0))
            put({
              order: [...a.order, ...b.order],
              cost: a.cost + b.cost + D[a.order[a.order.length - 1]][b.order[0]],
            });
    return [...bestBy.values()];
  }
  return arrange([...live][0]).reduce((lo, a) => (a.cost < lo.cost ? a : lo)).order;
}
