import { cn } from '@/lib/utils';
import { CLOUD_PROVIDER_LABELS, type CloudProviderId } from '@/types/cloud';

const SHORT: Record<CloudProviderId, string> = { aws: 'AWS', azure: 'AZ', gcp: 'GCP', digitalocean: 'DO' };

/** Compact provider tile + name (neutral ink — provider brand colours are not theme tokens). */
export function ProviderMark({ provider, withLabel = true, className }: { provider: CloudProviderId; withLabel?: boolean; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5', className)}>
      <span className="inline-flex h-5 min-w-7 items-center justify-center rounded-[5px] border border-border bg-muted px-1 font-mono text-[10px] font-semibold tracking-tight text-foreground">
        {SHORT[provider]}
      </span>
      {withLabel && <span className="text-xs">{CLOUD_PROVIDER_LABELS[provider]}</span>}
    </span>
  );
}
