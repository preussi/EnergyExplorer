// Typed wrappers around the backend REST API.

export interface DatasetInfo {
  name: string;
  is_default: boolean;
  /** session id — persist it to come back to this dataset after a refresh */
  id: string | null;
}

export interface Meta {
  axes: string[]; // 9 technologies (cost is not a design axis)
  units?: string[]; // physical unit per axis, aligned with `axes`
  axis_members?: Record<string, string[]>; // axes that lump several technologies
  methods: string[];
  spaces: string[];
  n_samples: number;
  // u_star = the optimum, techs only. c_star/cost_unit describe the cost budget
  // the near-optimal body is built around (cost ≤ (1+epsilon)·c_star).
  optimum: {
    u_star: number[]; epsilon: number; norm: number[];
    c_star?: number; cost_unit?: string;
  };
  diagnostics: { method: string; rhat: number[]; ess: number[] };
  dataset: DatasetInfo; // which data is currently loaded (default vs uploaded)
}

export interface Projection {
  method: string;
  sampler: string;
  dims: number;
  points: number[][]; // (n, dims)
  index: number[];
  optimum: number[] | null; // projected cost-optimum [x, y] (all methods)
  explained_variance: number[] | null;
  components: number[][] | null; // PCA loadings (dims x n_tech); null otherwise
  feature_names: string[]; // technology axis names matching `components`
  cached: boolean;
}

export interface SamplesData {
  fields: string[];
  values: number[][]; // (n, fields)
  index: number[];
}

export interface Cluster {
  x: number; // centroid in projection space
  y: number;
  count: number;
  top: { name: string; z: number; value: number }[];
}

export interface ClustersData {
  method: string;
  sampler: string;
  k: number;
  clusters: Cluster[];
}

export interface ColorData {
  field: string;
  values: number[];
  min: number;
  max: number;
  categorical: boolean;
}

// ---- dataset session ----
// Which dataset this browser is looking at. The backend keeps one per session
// (see backend/app/sessions.py), so every request has to say which one it means
// — otherwise a second user's build would swap the first user's data out. Sent
// as a header rather than a query param so it can't be lost by a caller
// assembling a URL by hand; the backend also accepts `?ds=` for shareable links.
const DS_KEY = "ee.datasetId";
let datasetId: string | null = null;

export function setDatasetId(id: string | null): void {
  datasetId = id;
  try {
    if (id) localStorage.setItem(DS_KEY, id);
    else localStorage.removeItem(DS_KEY);
  } catch {
    /* private mode / storage disabled — the id still works for this page view */
  }
}

export function getDatasetId(): string | null {
  return datasetId;
}

/** The id to boot with: an explicit `?ds=` in the URL wins over what this
 *  browser used last, so a shared link always opens the dataset it names. */
export function storedDatasetId(): string | null {
  const fromUrl = new URLSearchParams(location.search).get("ds");
  if (fromUrl) return fromUrl;
  try {
    return localStorage.getItem(DS_KEY);
  } catch {
    return null;
  }
}

/** Thrown-away marker: the backend no longer knows this session (404). */
export class UnknownDataset extends Error {}

// Retry only when fetch() itself rejects — a network-level blip ("Failed to
// fetch": server restart, dropped/aborted connection). HTTP errors (4xx/5xx)
// resolve and are handled by the callers, so a real 425/400 is never retried.
async function fetchRetry(url: string, init?: RequestInit, retries = 1): Promise<Response> {
  try {
    const headers = new Headers(init?.headers);
    if (datasetId) headers.set("X-Dataset-Id", datasetId);
    return await fetch(url, { ...init, headers });
  } catch (e) {
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 400));
      return fetchRetry(url, init, retries - 1);
    }
    throw e;
  }
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetchRetry(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const detail = body.detail ?? `${res.status} ${res.statusText}`;
    // 404 on a request that carried an id = that session is gone (swept, or the
    // link came from another deployment). Callers use this to offer a rebuild.
    if (res.status === 404 && datasetId) throw new UnknownDataset(detail);
    throw new Error(detail);
  }
  return res.json();
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetchRetry(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    throw new Error((b as { detail?: string }).detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const getMeta = () => getJSON<Meta>("/api/meta");

// ---- landing page: pick a preloaded polytope or upload one, then build ----
export interface PreloadedDataset {
  id: string;
  name: string;
  n_axes: number;
}

export interface DatasetList {
  datasets: PreloadedDataset[];
  /** hard ceiling on technology axes (uploads above this are a 422) */
  max_axes?: number;
  /** above this a build is slow enough to be worth predicting up front */
  warn_axes?: number;
}

export function getDatasets(): Promise<DatasetList> {
  return getJSON<DatasetList>("/api/datasets");
}

// Build the active dataset from a preloaded polytope: the backend generates
// `nSamples` samples over it and precomputes every view, then returns meta.
export function buildPreloaded(datasetId: string, nSamples: number): Promise<Meta> {
  return postJSON<Meta>("/api/build/preloaded", { dataset_id: datasetId, n_samples: nSamples });
}

// Same, from an uploaded polytope .npz. Surfaces the backend's per-key
// validation errors (422) as a single joined message.
export async function buildUpload(polytope: File, nSamples: number): Promise<Meta> {
  const fd = new FormData();
  fd.append("polytope", polytope);
  fd.append("n_samples", String(nSamples));
  const res = await fetchRetry("/api/build/upload", { method: "POST", body: fd });
  if (!res.ok) {
    const b = await res.json().catch(() => ({}));
    const d = (b as { detail?: unknown }).detail;
    let msg: string;
    if (d && typeof d === "object" && Array.isArray((d as { errors?: string[] }).errors)) {
      msg = (d as { errors: string[] }).errors.join(" · ");
    } else {
      msg = typeof d === "string" ? d : `${res.status} ${res.statusText}`;
    }
    throw new Error(msg);
  }
  return res.json();
}

export function getProjection(
  method: string,
  sampler: string,
  dims = 2,
  sample?: number,
): Promise<Projection> {
  const p = new URLSearchParams({ method, sampler, dims: String(dims) });
  if (sample) p.set("sample", String(sample));
  return getJSON<Projection>(`/api/projection?${p}`);
}

export function getColor(
  sampler: string,
  field: string,
  space = "phys",
): Promise<ColorData> {
  const p = new URLSearchParams({ sampler, field, space });
  return getJSON<ColorData>(`/api/color?${p}`);
}

export function getSamples(
  sampler: string,
  fields: string[],
  space = "phys",
  sample?: number,
): Promise<SamplesData> {
  const p = new URLSearchParams({ sampler, space, fields: fields.join(",") });
  // same N as getProjection → the backend serves the same seeded subset, so the
  // rows line up with the projected points (and we don't ship 100k unused rows).
  if (sample != null) p.set("sample", String(sample));
  return getJSON<SamplesData>(`/api/samples?${p}`);
}

export function getClusters(
  method: string,
  sampler: string,
  k = 6,
): Promise<ClustersData> {
  const p = new URLSearchParams({ method, sampler, k: String(k) });
  return getJSON<ClustersData>(`/api/clusters?${p}`);
}

export interface ExtremeDesign {
  axis: string;
  kind: "min" | "max";
  values: number[]; // physical, 9 technologies
  point: number[]; // base-PCA coordinates
}

export function getExtremes(sampler: string): Promise<{ sampler: string; extremes: ExtremeDesign[] }> {
  return getJSON(`/api/extremes?${new URLSearchParams({ sampler })}`);
}

export interface ConstraintInput {
  axis: string;
  min?: number | null;
  max?: number | null;
}

/** Which bounding-box corners a facet cannot reach, normalized to the unit
 *  square. 0 = that corner is attainable. See generate.py `corner_gaps`. */
export interface CornerGaps { LL: number; LR: number; UL: number; UR: number }

export type FacetCategory =
  | "independent"    // no corner cut — decide the two separately
  | "tradeoff"       // UR cut — cannot both be large
  | "dependency"     // LR/UL cut — one at scale requires the other (directional)
  | "at_least_one"   // LL cut — cannot both be small
  | "band"           // LL+UR — substitute one-for-one, total pinned
  | "locked"         // UL+LR — must move together
  | "unknown";

export interface FacetShape {
  category: FacetCategory;
  strength: number;   // size of the largest corner gap, 0..1
  cut: string[];      // which corners, strongest first
  detail: string;     // plain-language statement
  needs?: string;     // dependency only: the axis that requires…
  needed?: string;    // …this one
  /** the deciding gap is close enough to the threshold that the label could flip */
  borderline?: boolean;
}

export interface ShadowPair {
  x: string;
  y: string;
  boxiness: number | null;
  corner_gaps?: CornerGaps | null;
  shape?: FacetShape;
  /** the shadow outline, so the matrix draws all its minis from ONE response */
  polygon?: number[][] | null;
  /** true when the build's facet budget ran out before reaching this pair —
   *  fetch it individually via getShadow */
  pending?: boolean;
}

export interface Shadow {
  x: string;
  y: string;
  feasible: boolean;
  polygon: number[][]; // physical units, hull-ordered
  boxiness: number | null;
  optimum: [number, number];
  corner_gaps?: CornerGaps | null;
  shape?: FacetShape;
}

export interface FlexRange {
  axis: string;
  min: number | null;
  max: number | null;
}

export interface Flexibility {
  feasible: boolean;
  ranges: FlexRange[];
}

export function getShadowPairs(): Promise<{ pairs: ShadowPair[] }> {
  return getJSON("/api/shadow_pairs");
}

/** Transpose a facet: the API returns each pair once, in canonical axis order,
 *  but the matrix may need it as (column, row). LL/UR are fixed under transpose;
 *  LR and UL swap. */
export function flipGaps(g: CornerGaps): CornerGaps {
  return { LL: g.LL, UR: g.UR, LR: g.UL, UL: g.LR };
}

export type DependenceMetric = "dcor" | "mi" | "pearson";

export interface Dependence {
  sampler: string;
  axes: string[];
  n: number;
  dcor: number[][];    // distance correlation, [0,1], 0 iff independent
  mi: number[][];      // mutual information (nats), k-NN estimate
  pearson: number[][]; // linear baseline, [-1,1]
}

export function getDependence(sampler: string): Promise<Dependence> {
  return getJSON<Dependence>(`/api/dependence?${new URLSearchParams({ sampler })}`);
}

export function getShadow(
  x: string,
  y: string,
  constraints: ConstraintInput[] = [],
): Promise<Shadow> {
  return postJSON<Shadow>("/api/shadow", { x, y, constraints });
}

export function getFlexibility(constraints: ConstraintInput[] = []): Promise<Flexibility> {
  return postJSON<Flexibility>("/api/flexibility", { constraints });
}

export interface Marginals {
  /** the axes the projection was taken onto, in the order the values follow */
  axes: string[];
  /** uniform sample of pi_S(P), physical units */
  values: number[][];
  n: number;
  method: "base_cloud" | "hit_and_run_projection" | "degenerate" | "stalled" | "empty";
}

/** Distribution the Profiles violins draw, measured in the PROJECTION onto the
 *  shown axes (worldview B): reachable combinations of those axes, each counted
 *  once, rather than the full-space cloud read along one axis (which weights a
 *  combination by how many ways the hidden axes realize it — and so is identical
 *  whether or not an axis is hidden). Costs ~3.5 ms/sample, hence the debounce.
 *  With every axis shown the backend returns the base cloud unchanged. */
export function getMarginals(axes: string[], n = 1500): Promise<Marginals> {
  return postJSON<Marginals>("/api/marginals", { axes, n });
}

export interface GenerateResult {
  feasible: boolean;
  n: number;
  points: number[][]; // PCA-projected candidates
  values: number[][]; // physical units, all axes
  fields: string[];
  radius: number;
}

export function generate(body: {
  sampler: string;
  n: number;
  seed?: number;
  constraints: ConstraintInput[];
}): Promise<GenerateResult> {
  return postJSON<GenerateResult>("/api/generate", body);
}
