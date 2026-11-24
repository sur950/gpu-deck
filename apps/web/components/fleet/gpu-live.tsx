import { Flame, Gauge, Zap } from 'lucide-react';
import { thresholdBarClass } from '@/components/charts/chart-theme';
import { cn } from '@/lib/utils';
import type { Device } from '@/types/fleet';

function Meter({ percent, barClass }: { percent: number | null; barClass: string }) {
  const pct = percent === null ? 0 : Math.max(0, Math.min(100, percent));
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
      <div className={cn('h-full rounded-full transition-[width] duration-700 ease-out', percent === null ? 'bg-transparent' : barClass)} style={{ width: `${pct}%` }} />
    </div>
  );
}

/** Temperature bands for accelerators (°C): amber from 75, red from 85. */
export function tempBarClass(c: number | null | undefined): string {
  if (c === null || c === undefined) return 'bg-muted-foreground/40';
  if (c >= 85) return 'bg-error';
  if (c >= 75) return 'bg-warning';
  return 'bg-success';
}

interface GpuLiveProps {
  device: Pick<Device, 'live' | 'max_power_watts'>;
  className?: string;
  /** One row of three compact readouts (tables) instead of a stacked block. */
  inline?: boolean;
}

/** Live utilisation / power / temperature from the latest agent sample (or dashes when stale). */
export function GpuLive({ device, className, inline = false }: GpuLiveProps) {
  const live = device.live ?? {};
  const util = live.utilization_pct ?? null;
  const power = live.power_watts ?? null;
  const temp = live.temperature_c ?? null;
  const powerPct = power !== null && device.max_power_watts ? (power / device.max_power_watts) * 100 : null;
  const items = [
    { key: 'util', icon: Gauge, label: 'Util', value: util === null ? '—' : `${Math.round(util)}%`, pct: util, bar: thresholdBarClass(util ?? 0, 85, 97) },
    { key: 'power', icon: Zap, label: 'Power', value: power === null ? '—' : `${Math.round(power)} W`, pct: powerPct ?? (power !== null ? Math.min(100, power) : null), bar: 'bg-chart-2' },
    { key: 'temp', icon: Flame, label: 'Temp', value: temp === null ? '—' : `${Math.round(temp)}°C`, pct: temp === null ? null : Math.min(100, (temp / 100) * 100), bar: tempBarClass(temp) },
  ];
  if (inline) {
    return (
      <div className={cn('grid min-w-[180px] grid-cols-3 gap-2', className)}>
        {items.map((it) => (
          <div key={it.key} className="space-y-1" title={it.label}>
            <div className="text-[11px] font-medium tabular-nums">{it.value}</div>
            <Meter percent={it.pct} barClass={it.bar} />
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className={cn('grid grid-cols-3 gap-3', className)}>
      {items.map((it) => (
        <div key={it.key} className="space-y-1.5 rounded-lg border border-border bg-muted/30 px-3 py-2.5">
          <div className="flex items-center justify-between text-[11px] text-muted-foreground">
            <span>{it.label}</span>
            <it.icon className="size-3.5" />
          </div>
          <div className="text-lg font-semibold tabular-nums leading-none">{it.value}</div>
          <Meter percent={it.pct} barClass={it.bar} />
        </div>
      ))}
    </div>
  );
}
