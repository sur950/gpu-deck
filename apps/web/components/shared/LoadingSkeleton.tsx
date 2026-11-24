/**
 * Loading skeleton component
 * Used to show loading states across the app
 */

import { cn } from '@/lib/utils';

interface LoadingSkeletonProps {
  className?: string;
  lines?: number;
  circle?: boolean;
}

export function LoadingSkeleton({
  className,
  lines = 1,
  circle = false
}: LoadingSkeletonProps) {
  if (circle) {
    return (
      <div
        className={cn(
          'animate-pulse rounded-full bg-muted',
          className
        )}
      />
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-4 animate-pulse rounded bg-muted"
          style={{ width: i === lines - 1 ? '60%' : '100%' }}
        />
      ))}
    </div>
  );
}

export function LoadingSpinner({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center justify-center', className)}>
      <div className="size-7 animate-spin rounded-full border-[3px] border-muted border-t-foreground" />
    </div>
  );
}

export function FullPageLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <LoadingSpinner />
    </div>
  );
}
