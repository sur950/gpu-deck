'use client';

import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

export const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

interface TablePaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  /** Noun for the "results" text, e.g. "nodes". */
  noun?: string;
}

/**
 * Footer pagination row for list pages: "Showing x to y of z",
 * rows-per-page select, Previous / numbered / Next buttons.
 */
export function TablePagination({ page, pageSize, total, onPageChange, onPageSizeChange, noun = 'results' }: TablePaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), totalPages);
  const from = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const to = Math.min(current * pageSize, total);

  const numbers = Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
    if (totalPages <= 5) return i + 1;
    if (current <= 3) return i + 1;
    if (current >= totalPages - 2) return totalPages - 4 + i;
    return current - 2 + i;
  });

  return (
    <div className="mt-4 flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="text-sm text-muted-foreground">
        Showing {from} to {to} of {total} {noun}
      </div>
      <div className="flex flex-wrap items-center gap-4">
        {onPageSizeChange && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted-foreground">Rows per page:</span>
            <Select value={String(pageSize)} onValueChange={(v) => onPageSizeChange(Number(v))}>
              <SelectTrigger className="w-20" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PAGE_SIZE_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => onPageChange(current - 1)} disabled={current <= 1}>
            Previous
          </Button>
          <div className="flex items-center gap-1">
            {numbers.map((n) => (
              <Button
                key={n}
                variant={n === current ? 'default' : 'outline'}
                size="sm"
                className="w-9"
                onClick={() => onPageChange(n)}
              >
                {n}
              </Button>
            ))}
          </div>
          <Button variant="outline" size="sm" onClick={() => onPageChange(current + 1)} disabled={current >= totalPages}>
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}
