'use client';

import type { Layout, RefDevice, RefInstance } from '@/hooks/api/automation-api';
import { deviceLoads, deviceShortName, gib, itemsFromLayout } from '@/components/automation/layout-model';

interface LayoutMiniProps {
  layout: Layout;
  devices: RefDevice[];
  instances: RefInstance[];
  colors: Map<string, string>;
}

/** Per-GPU occupancy bars for a layout card (one thin bar per GPU, stacked by deployment). */
export function LayoutMini({ layout, devices, instances, colors }: LayoutMiniProps) {
  const loads = deviceLoads(itemsFromLayout(layout), devices, instances);
  return (
    <ul className="space-y-1.5">
      {devices.map((dev) => {
        const load = loads.get(dev.id);
        const used = load?.usedBytes ?? 0;
        return (
          <li key={dev.id} className="grid grid-cols-[110px_minmax(0,1fr)_64px] items-center gap-2 text-[11px]">
            <span className="truncate text-muted-foreground" title={`${dev.nodeName ?? ''} / ${dev.name}`}>
              {deviceShortName(dev)}
            </span>
            <span className="flex h-1.5 overflow-hidden rounded-full bg-muted">
              {(load?.segments ?? []).map((s) => (
                <span key={s.item.key} className="h-full" style={{ width: `${Math.min(100, (s.bytes / dev.vramTotalBytes) * 100)}%`, backgroundColor: colors.get(s.item.deploymentId) }} />
              ))}
            </span>
            <span className={load?.over ? 'text-right font-mono tabular-nums text-error-dark dark:text-error-light' : 'text-right font-mono tabular-nums text-muted-foreground'}>{gib(used)}</span>
          </li>
        );
      })}
    </ul>
  );
}
