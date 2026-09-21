import { describe, expect, it } from "vitest";

import {
  PERSONA_BENCH_POOL,
  PERSONA_PRODUCTION_1M_POOL,
  PERSONA_SAMPLE_SIZE_MAX_DEV,
  PERSONA_SAMPLE_SIZE_MAX_PRODUCTION,
} from "@/lib/types";

import { clampStrategySampleSize, sampleSizeMaxForPool } from "./personaSamplingTypes";

describe("sampleSizeMaxForPool", () => {
  it("gives the production pool the production ceiling", () => {
    expect(sampleSizeMaxForPool(PERSONA_PRODUCTION_1M_POOL)).toBe(
      PERSONA_SAMPLE_SIZE_MAX_PRODUCTION,
    );
  });

  it("treats a materialized 1M cohort as the production pool", () => {
    expect(
      sampleSizeMaxForPool("persona/datasets/matraix-persona-1m/cohorts/cohort-ec654f9f867d"),
    ).toBe(PERSONA_SAMPLE_SIZE_MAX_PRODUCTION);
  });

  it("holds the dev sample to the dev ceiling", () => {
    expect(sampleSizeMaxForPool(PERSONA_BENCH_POOL)).toBe(PERSONA_SAMPLE_SIZE_MAX_DEV);
  });

  it("falls back to the dev ceiling for an unknown or missing pool", () => {
    expect(sampleSizeMaxForPool(null)).toBe(PERSONA_SAMPLE_SIZE_MAX_DEV);
    expect(sampleSizeMaxForPool("")).toBe(PERSONA_SAMPLE_SIZE_MAX_DEV);
  });
});

describe("clampStrategySampleSize", () => {
  it("keeps a cohort-scale size on the production pool", () => {
    // The regression this covers: a saved 1000-persona setup used to come
    // back as 500, silently halving a cohort the user deliberately sized.
    expect(clampStrategySampleSize(1000, PERSONA_PRODUCTION_1M_POOL)).toBe(1000);
  });

  it("still clamps past the production ceiling", () => {
    expect(clampStrategySampleSize(99_999, PERSONA_PRODUCTION_1M_POOL)).toBe(
      PERSONA_SAMPLE_SIZE_MAX_PRODUCTION,
    );
  });

  it("clamps a large size on the dev sample", () => {
    expect(clampStrategySampleSize(1000, PERSONA_BENCH_POOL)).toBe(
      PERSONA_SAMPLE_SIZE_MAX_DEV,
    );
  });

  it("keeps a floor of 2 and rounds fractions", () => {
    expect(clampStrategySampleSize(1, PERSONA_PRODUCTION_1M_POOL)).toBe(2);
    expect(clampStrategySampleSize(10.6, PERSONA_PRODUCTION_1M_POOL)).toBe(11);
  });

  it("falls back to 2 for a non-finite size", () => {
    expect(clampStrategySampleSize(Number.NaN, PERSONA_PRODUCTION_1M_POOL)).toBe(2);
  });
});
