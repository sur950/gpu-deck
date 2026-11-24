/**
 * Backgrounds for generated initials tiles (user avatars, provider marks).
 *
 * On-brand only — the deep brand step (`--brand-strong`) and ink
 * (`--inverse`) — instead of a rainbow palette. Built from theme tokens, so a
 * custom NEXT_PUBLIC_BRAND_COLOR carries through. With the default brand
 * `--brand-strong` is the deep crimson #A10F28 in both modes, so the white
 * initials stay readable (8.0:1).
 */
const BRAND_TILE_BACKGROUNDS = [
  'rgb(var(--inverse))',
  'rgb(var(--brand-strong))',
  'linear-gradient(135deg, rgb(var(--brand-strong)) 0%, rgb(var(--inverse)) 100%)',
  'linear-gradient(135deg, rgb(var(--inverse)) 0%, rgb(var(--inverse)) 55%, rgb(var(--brand-strong)) 100%)',
] as const;

/** A stable background for `seed` (a name or initials), so a tile keeps its look across renders. */
export function brandTileBackground(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = seed.charCodeAt(i) + ((hash << 5) - hash);
  }
  return BRAND_TILE_BACKGROUNDS[Math.abs(hash) % BRAND_TILE_BACKGROUNDS.length];
}
