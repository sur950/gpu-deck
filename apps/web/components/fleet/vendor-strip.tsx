'use client';

import { cn } from '@/lib/utils';
import { VENDOR_STACK } from '@/components/fleet/vendor-badge';
import { formatBytes } from '@/utils/format';
import { DEVICE_VENDOR_LABELS, type Device, type DeviceVendor } from '@/types/fleet';

const ORDER: DeviceVendor[] = ['nvidia', 'amd', 'hygon', 'metax', 'moorethreads', 'thead', 'apple'];

interface VendorStripProps {
  devices: Device[];
  selected: DeviceVendor | '';
  onSelect: (vendor: DeviceVendor | '') => void;
  className?: string;
}

/**
 * The seven accelerator families GPU Deck has device plugins for, with how
 * many of each the fleet reports. Families with no hardware stay visible (and
 * dimmed) so it is obvious what the control plane can manage; clicking a tile
 * filters the inventory.
 */
export function VendorStrip({ devices, selected, onSelect, className }: VendorStripProps) {
  const stats = new Map<DeviceVendor, { count: number; vram: number; unhealthy: number }>();
  for (const d of devices) {
    const s = stats.get(d.vendor) ?? { count: 0, vram: 0, unhealthy: 0 };
    s.count += 1;
    s.vram += d.vram_total_bytes;
    if (d.health === 'unhealthy' || d.health === 'degraded') s.unhealthy += 1;
    stats.set(d.vendor, s);
  }
  return (
    <div className={cn('grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7', className)} role="group" aria-label="Filter by vendor">
      {ORDER.map((vendor) => {
        const s = stats.get(vendor);
        const active = selected === vendor;
        const empty = !s;
        return (
          <button
            key={vendor}
            type="button"
            aria-pressed={active}
            onClick={() => onSelect(active ? '' : vendor)}
            className={cn(
              'group relative flex flex-col items-start gap-1 overflow-hidden rounded-lg border px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
              active ? 'border-foreground/70 bg-card ring-1 ring-foreground/60' : 'border-border bg-card hover:border-foreground/25',
              empty && !active && 'bg-muted/20',
            )}
          >
            <span className={cn('absolute inset-x-0 top-0 h-[2px]', empty ? 'bg-transparent' : s.unhealthy ? 'bg-warning' : 'bg-success/70')} aria-hidden />
            <span className="flex w-full items-baseline justify-between gap-2">
              <span className={cn('text-[13px] font-semibold', empty && 'text-muted-foreground')}>{DEVICE_VENDOR_LABELS[vendor]}</span>
              <span className={cn('font-mono text-sm font-semibold tabular-nums', empty ? 'text-muted-foreground/60' : 'text-foreground')}>{s?.count ?? 0}</span>
            </span>
            <span className="text-[10.5px] text-muted-foreground">{VENDOR_STACK[vendor]}</span>
            <span className="text-[10.5px] tabular-nums text-muted-foreground">{s ? `${formatBytes(s.vram)} VRAM${s.unhealthy ? ` · ${s.unhealthy} degraded` : ''}` : 'Plugin ready · no devices'}</span>
          </button>
        );
      })}
    </div>
  );
}
