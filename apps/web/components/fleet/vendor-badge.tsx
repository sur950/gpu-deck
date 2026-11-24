import { cn } from '@/lib/utils';
import { DEVICE_VENDOR_LABELS, type DeviceVendor } from '@/types/fleet';

/** Short marks keep every vendor recognisable without remote logos (AS = Apple Silicon). */
const VENDOR_MARK: Record<DeviceVendor, string> = {
  nvidia: 'NV',
  amd: 'AMD',
  hygon: 'HY',
  metax: 'MX',
  moorethreads: 'MT',
  thead: 'TH',
  apple: 'AS',
};

interface VendorBadgeProps {
  vendor: DeviceVendor;
  className?: string;
  /** Show the full vendor name next to the mark. */
  withLabel?: boolean;
}

/** Software stack per vendor, as the device plugin reports it. */
export const VENDOR_STACK: Record<DeviceVendor, string> = {
  nvidia: 'CUDA · NVML',
  amd: 'ROCm · amd-smi',
  hygon: 'DTK · hy-smi',
  metax: 'MACA · mx-smi',
  moorethreads: 'MUSA · mthreads-gmi',
  thead: 'T-Head SDK',
  apple: 'Metal · MLX',
};

export function VendorBadge({ vendor, className, withLabel = true }: VendorBadgeProps) {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)} title={DEVICE_VENDOR_LABELS[vendor]}>
      <span className="flex h-5 min-w-5 items-center justify-center rounded-sm border border-border bg-muted/60 px-1 font-mono text-[9px] font-bold tracking-wide text-muted-foreground">
        {VENDOR_MARK[vendor]}
      </span>
      {withLabel && <span className="text-xs">{DEVICE_VENDOR_LABELS[vendor]}</span>}
    </span>
  );
}
