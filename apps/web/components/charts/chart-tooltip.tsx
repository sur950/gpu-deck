'use client';

/** One series row in the tooltip readout. */
export interface ChartTooltipRow {
  key: string;
  label: string;
  value: string;
  /** CSS colour of the series' line key. */
  color: string;
}

interface ChartTooltipCardProps {
  title: string;
  rows: ChartTooltipRow[];
}

/**
 * Tooltip readout shared by the dashboard charts: value first (strong),
 * series name second, each keyed by a short stroke of the series colour.
 */
export function ChartTooltipCard({ title, rows }: ChartTooltipCardProps) {
  return (
    <div className="min-w-[160px] rounded-lg border border-border bg-popover px-3 py-2.5 text-popover-foreground shadow-premium-lg">
      <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">{title}</p>
      <ul className="space-y-1">
        {rows.map((row) => (
          <li key={row.key} className="flex items-center gap-2 text-xs">
            <span aria-hidden className="h-0.5 w-3 rounded-full" style={{ backgroundColor: row.color }} />
            <span className="font-semibold tabular-nums">{row.value}</span>
            <span className="text-muted-foreground">{row.label}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
