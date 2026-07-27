// Typed wrappers around the backend REST API.

export interface DatasetInfo {
  name: string;
  is_default: boolean;
}

export interface Meta {
  axes: string[]; // 9 technologies (cost is not a design axis)
  methods: string[];
  spaces: string[];
  n_samples: number;
  optimum: { u_star: number[]; epsilon: number; norm: number[] }; // u_star = the optimum, techs only
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

// Retry only when fetch() itself rejects — a network-level blip ("Failed to
// fetch": server restart, dropped/aborted connection). HTTP errors (4xx/5xx)
// resolve and are handled by the callers, so a real 425/400 is never retried.
async function fetchRetry(url: string, init?: RequestInit, retries = 1): Promise<Response> {
  try {
    return await fetch(url, init);
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
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
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

export function getDatasets(): Promise<{ datasets: PreloadedDataset[] }> {
  return getJSON<{ datasets: PreloadedDataset[] }>("/api/datasets");
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
): Promise<SamplesData> {
  const p = new URLSearchParams({ sampler, space, fields: fields.join(",") });
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

export interface ShadowPair {
  x: string;
  y: string;
  boxiness: number;
}

export interface Shadow {
  x: string;
  y: string;
  feasible: boolean;
  polygon: number[][]; // physical units, hull-ordered
  boxiness: number | null;
  optimum: [number, number];
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

export interface VolumeEstimate {
  feasible: boolean;
  ratio: number;         // vol(constrained) / vol(full), in [0,1]
  log10: number | null;  // log10(ratio); null when ratio == 0
  levels: number;        // subset-simulation levels used (0 = direct estimate)
  method: "trivial" | "direct" | "subset_simulation" | "empty";
  cv: number;            // coefficient of variation (approx relative std of ratio)
}

export function getVolume(constraints: ConstraintInput[] = []): Promise<VolumeEstimate> {
  return postJSON<VolumeEstimate>("/api/volume", { constraints });
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
