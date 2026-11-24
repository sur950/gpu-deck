import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { PAGE_CLASS } from '@/components/shared/PageContainer';

interface ListPageSkeletonProps {
  /** Stat cards under the header (0 hides the row). */
  stats?: number;
  /** Table rows. */
  rows?: number;
}

/**
 * Mirrors a list page (header + stat cards + table card with pagination) so
 * content lands where the placeholders were, inside the page container.
 */
export function ListPageSkeleton({ stats = 4, rows = 8 }: ListPageSkeletonProps) {
  return (
    <div className={PAGE_CLASS}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="space-y-2">
          <Skeleton className="h-9 w-28" />
          <Skeleton className="h-4 w-64" />
        </div>
        <div className="flex items-center gap-3">
          <Skeleton className="h-9 w-44" />
          <Skeleton className="h-8 w-32" />
          <Skeleton className="h-8 w-28" />
        </div>
      </div>

      {/* Stats */}
      {stats > 0 && (
        <div className="grid gap-4 md:grid-cols-4">
          {Array.from({ length: stats }).map((_, i) => (
            <Card key={i} className="rounded-md shadow-premium">
              <CardContent>
                <div className="flex items-center justify-between mb-2">
                  <Skeleton className="h-4 w-28" />
                  <Skeleton className="h-4 w-4 rounded" />
                </div>
                <Skeleton className="h-8 w-16 mb-1" />
                <Skeleton className="h-3 w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Table Card */}
      <Card className="rounded-md shadow-premium">
        <CardHeader>
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-96" />
            <Skeleton className="h-9 w-44" />
          </div>
        </CardHeader>
        <CardContent>
          {/* Table header */}
          <div className="grid grid-cols-8 gap-4 pb-3 border-b">
            <Skeleton className="h-4 w-24 col-span-2" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-14" />
            <Skeleton className="h-4 w-20 ml-auto" />
            <Skeleton className="h-4 w-24 ml-auto" />
            <Skeleton className="h-4 w-20 ml-auto" />
            <Skeleton className="h-4 w-20 ml-auto" />
          </div>

          {/* Table rows */}
          <div className="divide-y">
            {Array.from({ length: rows }).map((_, i) => (
              <div key={i} className="grid grid-cols-8 gap-4 py-3 items-center">
                <div className="col-span-2 space-y-1">
                  <div className="flex items-center gap-2">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-5 w-14 rounded-full" />
                  </div>
                  <Skeleton className="h-3 w-44" />
                </div>
                <Skeleton className="h-5 w-20 rounded-full" />
                <div className="flex items-center gap-1.5">
                  <Skeleton className="h-4 w-4 rounded-full" />
                  <Skeleton className="h-4 w-14" />
                </div>
                <Skeleton className="h-4 w-14 ml-auto" />
                <Skeleton className="h-4 w-16 ml-auto" />
                <Skeleton className="h-4 w-10 ml-auto" />
                <Skeleton className="h-4 w-16 ml-auto" />
              </div>
            ))}
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4 pt-4 border-t">
            <Skeleton className="h-4 w-48" />
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-20" />
              </div>
              <div className="flex items-center gap-2">
                <Skeleton className="h-8 w-20" />
                <Skeleton className="h-8 w-9" />
                <Skeleton className="h-8 w-9" />
                <Skeleton className="h-8 w-9" />
                <Skeleton className="h-8 w-16" />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
