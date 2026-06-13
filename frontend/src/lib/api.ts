// Typed wrappers around the backend REST API.

export interface Meta {
  axes: string[];
  samplers: string[];
  methods: string[];
  spaces: string[];
  n_samples: number;
  cost_axis: string;
  optimum: { u_star: number[]; c_star: number; epsilon: number; norm: number[] };
  diagnostics: Record<string, { rhat: number[]; ess: number[] }>;
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

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
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
  values: number[]; // physical, all 10 axes
  point: number[]; // base-PCA coordinates
  cost: number;
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
