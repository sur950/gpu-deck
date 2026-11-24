import { cn } from '@/lib/utils';

export interface DetailItem {
  label: string;
  value: React.ReactNode;
  /** Render the value in a mono font (ids, paths, hashes). */
  mono?: boolean;
  /** Span both columns in the two-column layout. */
  wide?: boolean;
}

interface DetailListProps {
  items: DetailItem[];
  /** 1 = stacked rows (sidebars), 2 = two-column grid (wide cards). */
  columns?: 1 | 2;
  className?: string;
}

/**
 * Label / value rows in a muted-pill style
 * (`px-3 py-2 bg-muted/50 rounded-lg`), used by every detail page.
 */
export function DetailList({ items, columns = 1, className }: DetailListProps) {
  return (
    <dl className={cn('grid gap-1.5', columns === 2 && 'sm:grid-cols-2', className)}>
      {items.map((item) => (
        <div
          key={item.label}
          className={cn(
            'flex items-start justify-between gap-4 rounded-lg bg-muted/50 px-3 py-2',
            item.wide && columns === 2 && 'sm:col-span-2',
          )}
        >
          <dt className="shrink-0 text-xs text-muted-foreground">{item.label}</dt>
          <dd
            className={cn(
              'min-w-0 text-right text-xs font-medium break-all',
              item.mono && 'font-mono text-[11px]',
            )}
          >
            {item.value ?? <span className="text-muted-foreground">—</span>}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Small uppercase section label used above groups of details. */
export function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return <p className={cn('text-xs font-medium tracking-wide text-muted-foreground uppercase', className)}>{children}</p>;
}
