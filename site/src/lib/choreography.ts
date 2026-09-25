/**
 * The colour choreography shared by the mark and the hero surface.
 *
 * The mark is the volatility surface seen edge-on, so the two should move as
 * one object: when a colour sweeps through the masthead it sweeps through the
 * surface's ribs at the same moment. That only holds if both read the same
 * clock and the same rules, which is what this module is for. Two copies of
 * the logic would drift the first time either was tuned.
 *
 * The clock is performance.now() itself, milliseconds since navigation, rather
 * than time since each component started. The hero mounts after fetching its
 * data and the mark mounts immediately, so per-component start times differ by
 * however long the fetch took, and the two would never quite line up.
 */

/** The Pantone Ultra Violet family, in the order the colours are walked:
 *  Ultra Violet, Sparkling Grape, Mulberry, Jacaranda, Rhapsody, Red Violet,
 *  Chateau Rose, Prism Pink. Spot colours have no exact sRGB equivalent; these
 *  are the standard approximations, as 0-255 channels. */
export const RAMP: readonly [number, number, number][] = [
  [0x5f, 0x4b, 0x8b], // Ultra Violet    18-3838
  [0x8b, 0x4a, 0x8b], // Sparkling Grape 19-3336
  [0xa9, 0x6f, 0xa5], // Mulberry        17-3014
  [0x7d, 0x8b, 0xc7], // Jacaranda       17-3930
  [0xa0, 0x93, 0xc0], // Rhapsody        16-3817
  [0xa8, 0x5c, 0x82], // Red Violet      17-1818
  [0xd9, 0x73, 0x8f], // Chateau Rose    17-2120
  [0xf0, 0xa6, 0xce], // Prism Pink      14-2311
];

/** One colour every this many seconds, so a lap of eight takes about 26. */
export const SECONDS_PER_COLOUR = 3.2;
/** Share of each step spent sweeping; the remainder the stack rests uniform.
 *  0.83 keeps the mark's own timing where it was tuned before this module
 *  existed, to within a few milliseconds. */
export const BAND_SPAN = 0.83;
/** Above 1, neighbouring bands blend into each other rather than snapping. */
export const OVERLAP = 1.9;

/** Which colour step the page is on, and how far through it. */
export function clockPhase(now: number = performance.now()) {
  const phase = now / 1000 / SECONDS_PER_COLOUR;
  const step = Math.floor(phase);
  return { step, w: phase - step };
}

/**
 * How far a band has turned from colour `step` to colour `step + 1`, eased.
 *
 * `depth` runs 0 to 1 across the stack. Even steps sweep from depth 1 to 0 and
 * odd steps back again, so a change crosses the stack and the next one returns,
 * instead of every sweep snapping back to the same starting edge.
 *
 * The sweep starts at the beginning of a step and ends at exactly BAND_SPAN of
 * it, whatever the band count: each band's own transition narrows as bands are
 * added, and the slots are spread over what remains. That is what keeps a
 * six-curve mark and a thirty-rib surface starting and settling together. The
 * first version spread slots over a fixed span instead, which put the end of
 * the sweep at BAND_SPAN * (1 + (OVERLAP - 1) / bands): the surface settled a
 * quarter of a second before the mark did.
 */
export function bandProgress(depth: number, bands: number, step: number, w: number): number {
  const n = Math.max(bands, 1);
  const order = step % 2 === 0 ? 1 - depth : depth;
  const width = Math.min((BAND_SPAN / n) * OVERLAP, BAND_SPAN);
  const slot = order * (BAND_SPAN - width);
  const f = Math.min(Math.max((w - slot) / width, 0), 1);
  return f * f * (3 - 2 * f);
}

/** Channels of the colour `f` of the way from step `i` to step `j`. */
export function mixRgb(i: number, j: number, f: number): [number, number, number] {
  const n = RAMP.length;
  const a = RAMP[((i % n) + n) % n];
  const b = RAMP[((j % n) + n) % n];
  return [0, 1, 2].map((c) => Math.round(a[c] + (b[c] - a[c]) * f)) as [number, number, number];
}

export function mixCss(i: number, j: number, f: number): string {
  const [r, g, b] = mixRgb(i, j, f);
  return `rgb(${r},${g},${b})`;
}
