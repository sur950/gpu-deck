'use client';

import { Box, Cloud, CloudCog, Container, Droplets, Globe, Hash, KeyRound, KeySquare, LockKeyhole, Mail, Ship, Smile, SquareTerminal, Webhook } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { CredentialCategory } from '@/types/vault';

export const KIND_ICONS: Record<string, LucideIcon> = {
  aws: Cloud,
  azure: CloudCog,
  gcp: Globe,
  digitalocean: Droplets,
  huggingface: Smile,
  modelscope: Box,
  slack: Hash,
  smtp: Mail,
  webhook: Webhook,
  ssh_key: SquareTerminal,
  container_registry: Container,
  kubernetes: Ship,
  generic: LockKeyhole,
};

/** Category tint (theme tokens only): cloud blue, model sources amber, notifications violet, access aqua. */
export const CATEGORY_TINT: Record<CredentialCategory | 'legacy' | 'system', string> = {
  cloud: 'bg-chart-2/10 text-chart-2 ring-chart-2/25',
  model_source: 'bg-warning/10 text-warning-dark ring-warning/30 dark:text-warning-light',
  notification: 'bg-chart-4/10 text-chart-4 ring-chart-4/25',
  access: 'bg-chart-3/10 text-chart-3 ring-chart-3/25',
  other: 'bg-muted text-muted-foreground ring-border',
  legacy: 'bg-muted text-muted-foreground ring-border',
  system: 'bg-error/10 text-error-dark ring-error/25 dark:text-error-light',
};


interface KindGlyphProps {
  kind: string | null | undefined;
  category?: CredentialCategory | 'legacy' | 'system';
  size?: 'xs' | 'sm' | 'md' | 'lg';
  className?: string;
}

const SIZES = {
  xs: { box: 'size-5 rounded-[5px]', icon: 'size-3' },
  sm: { box: 'size-7 rounded-md', icon: 'size-3.5' },
  md: { box: 'size-9 rounded-lg', icon: 'size-4' },
  lg: { box: 'size-11 rounded-xl', icon: 'size-5' },
};

/** The kind's icon on its category tint — the one visual cue every credential row and picker shares. */
export function KindGlyph({ kind, category = 'other', size = 'sm', className }: KindGlyphProps) {
  const Icon = category === 'system' ? KeyRound : (KIND_ICONS[kind ?? ''] ?? KeySquare);
  const s = SIZES[size];
  return (
    <span aria-hidden className={cn('inline-flex shrink-0 items-center justify-center ring-1 ring-inset', s.box, CATEGORY_TINT[category], className)}>
      <Icon className={s.icon} />
    </span>
  );
}
