'use client';

import { useId } from 'react';
import { cn } from '@/lib/utils';
import { GLYPH_MEDIUM, GLYPH_SMALL, GLYPH_TILE, GLYPH_TILE_SMALL } from '@/components/shared/gpu-deck-glyph';

interface GpuDeckMarkProps {
  /**
   * Rendered size in CSS pixels: the side of the square box ('square'), or the
   * height of the glyph's own box ('tight', 4:3 wide, for lockups).
   */
  size: number;
  /** 'square' centres the glyph in a square (icons, collapsed nav); 'tight' hugs it. */
  fit?: 'square' | 'tight';
  className?: string;
  /** Accessible name. Empty (decorative) by default: callers place it beside text that names the product. */
  title?: string;
}

/**
 * The GPU Deck mark: a graphics card (bracket, shroud with the cut corner, two
 * fans, edge connector), drawn inline in `currentColor` from the same geometry
 * as assets/brand (path data generated into gpu-deck-glyph.ts), so it stays
 * sharp at every size, follows the text colour in light and dark mode, and
 * needs no request. At 20 px and below it switches to the 16 px drawing, whose
 * edges land on whole pixels.
 */
export function GpuDeckMark({ size, fit = 'square', className, title = '' }: GpuDeckMarkProps) {
  const small = fit === 'square' && size <= 20;
  const paths = small ? GLYPH_SMALL : GLYPH_MEDIUM;
  const tight = fit === 'tight';
  return (
    <svg
      viewBox={small ? '0 0 64 64' : tight ? '4 12 56 42' : '0 1 64 64'}
      width={tight ? Math.round((size * 56) / 42) : size}
      height={size}
      fill="currentColor"
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title || undefined}
      className={cn('shrink-0 select-none', className)}
    >
      {paths.map((p, i) => (
        <path key={i} d={p.d} fillRule={p.evenOdd ? 'evenodd' : undefined} />
      ))}
    </svg>
  );
}

interface GpuDeckTileProps {
  /** Side of the tile in CSS pixels. */
  size: number;
  className?: string;
}

/**
 * The app icon form of the mark: the white glyph on the crimson tile
 * (#D3263F to #A10F28, lit from above, hairline edge; assets/brand/mark.svg),
 * the same in light and dark mode like the desktop and PWA icons. The glyph
 * follows the optical sizes of the PNG icons: the 16 px drawing up to 20 px,
 * the ring-fan tile drawing up to 96 px, the bladed drawing above.
 */
export function GpuDeckTile({ size, className }: GpuDeckTileProps) {
  const id = useId().replace(/:/g, '');
  const large = size > 96;
  const paths = size <= 20 ? GLYPH_TILE_SMALL : large ? GLYPH_MEDIUM : GLYPH_TILE;
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden className={cn('shrink-0 select-none', className)}>
      <defs>
        <linearGradient id={`${id}-t`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#D3263F" />
          <stop offset="1" stopColor="#A10F28" />
        </linearGradient>
        <radialGradient id={`${id}-g`} cx="0.5" cy="0.08" r="0.75">
          <stop offset="0" stopColor="#FFFFFF" stopOpacity="0.11" />
          <stop offset="1" stopColor="#FFFFFF" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill={`url(#${id}-t)`} />
      <rect width="64" height="64" rx="14" fill={`url(#${id}-g)`} />
      <rect x="0.5" y="0.5" width="63" height="63" rx="13.5" fill="none" stroke="#FFFFFF" strokeOpacity="0.2" />
      <g transform={large ? 'translate(6.4 6.4) scale(0.8)' : undefined} fill="#FFFFFF">
        {paths.map((p, i) => (
          <path key={i} d={p.d} fillRule={p.evenOdd ? 'evenodd' : undefined} />
        ))}
      </g>
    </svg>
  );
}
