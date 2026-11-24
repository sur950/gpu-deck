import { cn } from '@/lib/utils';

interface JsonViewProps {
  value: unknown;
  className?: string;
  /** Collapse to this many lines with a scroll (default: unlimited). */
  maxHeight?: number;
}

/** Monospace, theme-tinted JSON block for decision payloads, before/after states and plans. */
export function JsonView({ value, className, maxHeight }: JsonViewProps) {
  const text = value === undefined ? '—' : JSON.stringify(value, null, 2);
  return (
    <pre
      className={cn(
        'overflow-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed text-foreground',
        className,
      )}
      style={maxHeight ? { maxHeight } : undefined}
    >
      {text}
    </pre>
  );
}
