import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

interface LabelChipsProps {
  labels: Record<string, string> | undefined | null;
  /** Show at most this many, then "+n". */
  max?: number;
  className?: string;
  emptyText?: string;
}

/** `key=value` outline chips for node / cluster labels. */
export function LabelChips({ labels, max = 3, className, emptyText }: LabelChipsProps) {
  const entries = Object.entries(labels ?? {});
  if (entries.length === 0) {
    return emptyText ? <span className="text-xs text-muted-foreground">{emptyText}</span> : null;
  }
  const shown = entries.slice(0, max);
  const rest = entries.length - shown.length;
  return (
    <div className={cn('flex flex-wrap items-center gap-1', className)}>
      {shown.map(([key, value]) => (
        <Badge key={key} variant="outline" className="h-auto px-1.5 py-0 font-mono text-[11px] font-normal">
          <span className="text-muted-foreground">{key}=</span>
          {value}
        </Badge>
      ))}
      {rest > 0 && (
        <span className="text-[11px] text-muted-foreground" title={entries.slice(max).map(([k, v]) => `${k}=${v}`).join('\n')}>
          +{rest}
        </span>
      )}
    </div>
  );
}
