/**
 * Deterministic randomness for fixtures.
 *
 * Every fixture value is a pure function of (seed, time), so a page refetch
 * continues the same curves instead of redrawing them, and two screens that
 * show the same metric agree.
 */

/** FNV-1a 32-bit hash of a string. */
export function hash32(input: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < input.length; i += 1) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Small fast PRNG (mulberry32) seeded from a number. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Uniform [0, 1) value for an integer lattice point. */
function lattice(seed: number, x: number): number {
  return mulberry32(hash32(`${seed}:${x}`))();
}

function smoothstep(t: number): number {
  return t * t * (3 - 2 * t);
}

/** Value noise in [-1, 1]: smooth, continuous, deterministic per seed. */
export function smoothNoise(seed: number, x: number): number {
  const x0 = Math.floor(x);
  const t = smoothstep(x - x0);
  const a = lattice(seed, x0) * 2 - 1;
  const b = lattice(seed, x0 + 1) * 2 - 1;
  return a + (b - a) * t;
}

/** Fractal sum of value noise, still in roughly [-1, 1]. */
export function fbm(seed: number, x: number, octaves = 3): number {
  let sum = 0;
  let amp = 1;
  let norm = 0;
  let freq = 1;
  for (let i = 0; i < octaves; i += 1) {
    sum += smoothNoise(seed + i * 101, x * freq) * amp;
    norm += amp;
    amp *= 0.5;
    freq *= 2.1;
  }
  return sum / norm;
}

/** A stable RFC-4122-looking id derived from a label. */
export function uuid(label: string): string {
  const a = hash32(label).toString(16).padStart(8, '0');
  const b = hash32(`${label}#1`).toString(16).padStart(8, '0');
  const c = hash32(`${label}#2`).toString(16).padStart(8, '0');
  const d = hash32(`${label}#3`).toString(16).padStart(8, '0');
  const hex = `${a}${b}${c}${d}`;
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-4${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

/** 16-hex span id / 32-hex trace id from a label. */
export function hexId(label: string, length: 16 | 32): string {
  let out = '';
  let i = 0;
  while (out.length < length) {
    out += hash32(`${label}:${i}`).toString(16).padStart(8, '0');
    i += 1;
  }
  return out.slice(0, length);
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function round(value: number, decimals = 1): number {
  const f = 10 ** decimals;
  return Math.round(value * f) / f;
}

/** Pick an element deterministically by seed. */
export function pick<T>(items: readonly T[], seed: string): T {
  return items[hash32(seed) % items.length];
}
