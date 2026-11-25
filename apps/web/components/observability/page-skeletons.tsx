import { Skeleton } from '@/components/ui/skeleton';
import { PAGE_CLASS } from '@/components/shared/PageContainer';

/** Mirrors a metrics page: header, filter row, stat tiles, chart grid. */
export function MetricsPageSkeleton({ tiles = 6, charts = 4, tall = false }: { tiles?: number; charts?: number; tall?: boolean }) {
  return (
    <div className={PAGE_CLASS}>
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-9 w-48" />
          <Skeleton className="h-4 w-80" />
        </div>
        <Skeleton className="h-9 w-28 rounded-full" />
      </div>
      <div className="flex flex-wrap gap-2">
        <Skeleton className="h-9 w-72 rounded-full" />
        <Skeleton className="h-9 w-32 rounded-full" />
        <Skeleton className="h-9 w-44 rounded-full" />
      </div>
      {tiles > 0 && (
        <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: tiles }).map((_, i) => (
            <Skeleton key={i} className="h-[116px] rounded-xl" />
          ))}
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {Array.from({ length: charts }).map((_, i) => (
          <Skeleton key={i} className={tall ? 'h-[340px] rounded-xl' : 'h-[300px] rounded-xl'} />
        ))}
      </div>
    </div>
  );
}

/** Mirrors a list-and-detail page (logs): header, filters, one long card. */
export function StreamPageSkeleton({ rows = 14 }: { rows?: number }) {
  return (
    <div className={PAGE_CLASS}>
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-9 w-32" />
          <Skeleton className="h-4 w-80" />
        </div>
        <Skeleton className="h-9 w-40 rounded-full" />
      </div>
      <div className="rounded-xl border border-border bg-card p-4 shadow-premium">
        <div className="mb-4 flex flex-wrap gap-2">
          <Skeleton className="h-9 w-80" />
          <Skeleton className="h-9 w-40" />
          <Skeleton className="h-9 w-40" />
        </div>
        <div className="space-y-2">
          {Array.from({ length: rows }).map((_, i) => (
            <Skeleton key={i} className="h-5" style={{ width: `${70 + ((i * 37) % 30)}%` }} />
          ))}
        </div>
      </div>
    </div>
  );
}
