/** Persona sampling state shared by the cockpit left rail. */

import type { TaskPersonaStrategy } from "@/lib/types";
import {
  PERSONA_PRODUCTION_1M_POOL,
  PERSONA_SAMPLE_SIZE_MAX_DEV,
  PERSONA_SAMPLE_SIZE_MAX_PRODUCTION,
} from "@/lib/types";

/**
 * Max sample size for a pool. The dev sample is small enough that a large
 * request is a mistake; the production 1M coreset supports cohort-scale runs
 * (n=1000 and up), so clamping it to the dev ceiling silently shrinks a cohort
 * the user deliberately sized.
 */
export function sampleSizeMaxForPool(pool: string | null | undefined): number {
  const value = (pool ?? "").trim();
  const isProduction1m =
    value === PERSONA_PRODUCTION_1M_POOL || value.includes("/matraix-persona-1m/cohorts/");
  return isProduction1m ? PERSONA_SAMPLE_SIZE_MAX_PRODUCTION : PERSONA_SAMPLE_SIZE_MAX_DEV;
}

/** Clamp a strategy-supplied sample size against the ceiling its pool allows. */
export function clampStrategySampleSize(value: number, pool: string | null | undefined): number {
  if (!Number.isFinite(value)) return 2;
  return Math.min(sampleSizeMaxForPool(pool), Math.max(2, Math.round(value)));
}

export type PersonaSamplingMode = "single" | "random" | "stratified" | "all";

export type StratifiedAllocation = "perCell" | "proportional" | "equalTotal";

export interface PersonaDimensionFilters {
  sources: string[];
  /** dimension id → selected values (multi-select per dimension). */
  dimensionFilters: Record<string, string[]>;
}

export function emptyPersonaDimensionFilters(): PersonaDimensionFilters {
  return { sources: [], dimensionFilters: {} };
}

export function activeFilterCount(filters: PersonaDimensionFilters): number {
  const dimCount = Object.values(filters.dimensionFilters).filter((values) => values.length > 0).length;
  return filters.sources.length + dimCount;
}

export function filtersForSampleApi(
  filters: PersonaDimensionFilters,
): Record<string, string | string[]> | undefined {
  const entries = Object.entries(filters.dimensionFilters).filter(([, values]) => values.length > 0);
  if (entries.length === 0) return undefined;
  return Object.fromEntries(entries.map(([key, values]) => [key, values.length === 1 ? values[0] : values]));
}

export interface StrategySamplingView {
  mode: PersonaSamplingMode;
  fields: string[];
  allocation: StratifiedAllocation;
  sampleSize: number | null;
  perCell: number | null;
}

function asSamplingMode(value: string | null | undefined): PersonaSamplingMode {
  if (value === "random" || value === "stratified" || value === "all" || value === "single") {
    return value;
  }
  return "single";
}

function asAllocation(
  value: string | null | undefined,
  fallback: StratifiedAllocation,
): StratifiedAllocation {
  if (value === "perCell" || value === "proportional" || value === "equalTotal") {
    return value;
  }
  return fallback;
}

/** Read the unified ``strategy.sampling`` block (required on valid strategies). */
export function readStrategySampling(
  strategy: TaskPersonaStrategy | null | undefined,
): StrategySamplingView {
  const sampling = strategy?.sampling;
  if (sampling && typeof sampling === "object") {
    const mode = asSamplingMode(sampling.mode);
    const fields = Array.isArray(sampling.fields)
      ? sampling.fields.filter(
          (field): field is string => typeof field === "string" && Boolean(field.trim()),
        )
      : [];
  const sampleSize =
    typeof sampling.sampleSize === "number" && sampling.sampleSize > 0
      ? Math.round(sampling.sampleSize)
      : typeof (sampling as { sample_size?: number }).sample_size === "number"
        ? Math.round((sampling as { sample_size?: number }).sample_size as number)
        : null;
  const perCell =
    typeof sampling.perCell === "number" && sampling.perCell >= 1
      ? Math.round(sampling.perCell)
      : typeof (sampling as { per_cell?: number }).per_cell === "number"
        ? Math.round((sampling as { per_cell?: number }).per_cell as number)
        : null;
    const allocationFallback: StratifiedAllocation =
      perCell != null ? "perCell" : sampleSize != null ? "equalTotal" : "perCell";
    return {
      mode: mode === "single" && fields.length > 0 ? "stratified" : mode,
      fields,
      allocation: asAllocation(sampling.allocation, allocationFallback),
      sampleSize,
      perCell,
    };
  }

  const legacy = strategy as
    | (TaskPersonaStrategy & {
        defaultMode?: string;
        stratifyFields?: string[];
        sampleSizePerValueGroup?: number;
        sampleSize?: number;
      })
    | null
    | undefined;
  const fields = Array.isArray(legacy?.stratifyFields)
    ? legacy.stratifyFields.filter(
        (field): field is string => typeof field === "string" && Boolean(field.trim()),
      )
    : [];
  const perCell =
    typeof legacy?.sampleSizePerValueGroup === "number" && legacy.sampleSizePerValueGroup >= 1
      ? Math.round(legacy.sampleSizePerValueGroup)
      : null;
  const sampleSize =
    typeof legacy?.sampleSize === "number" && legacy.sampleSize > 0
      ? Math.round(legacy.sampleSize)
      : null;
  const mode = asSamplingMode(legacy?.defaultMode);
  return {
    mode: fields.length > 0 ? "stratified" : mode,
    fields,
    allocation: perCell != null ? "perCell" : sampleSize != null ? "equalTotal" : "perCell",
    sampleSize,
    perCell,
  };
}
