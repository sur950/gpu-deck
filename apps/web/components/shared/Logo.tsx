'use client';

import { useState } from 'react';
import type { MouseEvent } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import { cn } from '@/lib/utils';
import { useAuth } from '@/hooks/use-auth';
import { APP_BYLINE, APP_NAME, LOGO_URL } from '@/utils/brand-env';
import { GpuDeckTile } from '@/components/shared/gpu-deck-mark';

interface LogoProps {
  className?: string;
  /** When true shows only the square icon mark (for collapsed sidebars) */
  iconOnly?: boolean;
  /** Extra click handler — the mobile nav drawer closes itself with it. */
  onClick?: (event: MouseEvent<HTMLAnchorElement>) => void;
  /** 'onDark' for dark surfaces: light lockup text and focus ring. */
  tone?: 'default' | 'onDark';
}

/**
 * The product lockup: the crimson GPU Deck tile with the product name and its
 * descriptor line, both from NEXT_PUBLIC_APP_NAME / NEXT_PUBLIC_APP_BYLINE.
 * A custom NEXT_PUBLIC_LOGO_URL replaces the lockup (white-label logo).
 */
export function Logo({ className, iconOnly = false, onClick, tone = 'default' }: LogoProps) {
  const onDark = tone === 'onDark';
  const { isAuthenticated } = useAuth();
  // Remember a failed URL rather than a one-way flag, so a corrected env value
  // after a rebuild is tried again instead of staying hidden.
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  const customLogoSrc = LOGO_URL && LOGO_URL !== failedSrc ? LOGO_URL : null;
  const href = isAuthenticated ? '/dashboard' : '/login';

  return (
    <Link
      href={href}
      onClick={onClick}
      aria-label={customLogoSrc ? APP_NAME : `${APP_NAME}, ${APP_BYLINE}`}
      className={cn(
        'inline-flex items-center rounded-md outline-none transition-opacity hover:opacity-80 focus-visible:ring-2',
        onDark ? 'focus-visible:ring-inverse-foreground/50' : 'focus-visible:ring-ring/30',
        className,
      )}
    >
      {iconOnly ? (
        <GpuDeckTile size={32} className="drop-shadow-sm" />
      ) : customLogoSrc ? (
        // Fixed height, capped width: any aspect ratio (square marks, wide
        // wordmarks) fits the 56 px header instead of a square logo growing
        // 160 px tall over the navigation.
        <Image src={customLogoSrc} alt="" width={160} height={40} unoptimized style={{ objectFit: 'contain', objectPosition: 'left center', height: '40px', width: 'auto', maxWidth: '160px' }} onError={() => setFailedSrc(customLogoSrc)} priority />
      ) : (
        // No custom logo: the product's own identity — the mark with the
        // product name and its descriptor.
        <span className="inline-flex items-center gap-2.5">
          <GpuDeckTile size={32} className="drop-shadow-sm" />
          <span className="flex flex-col leading-none">
            <span className={cn('text-[15px] font-bold tracking-tight', onDark ? 'text-inverse-foreground' : 'text-foreground')}>{APP_NAME}</span>
            <span className={cn('mt-1 text-[11px] font-medium tracking-wide', onDark ? 'text-inverse-muted-foreground' : 'text-muted-foreground')}>{APP_BYLINE}</span>
          </span>
        </span>
      )}
    </Link>
  );
}
