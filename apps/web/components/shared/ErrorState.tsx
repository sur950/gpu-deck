/**
 * ErrorState component
 * Shared component for displaying error states with optional retry action
 */

import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = 'Something went wrong',
  description = 'An unexpected error occurred. Please try again.',
  onRetry,
}: ErrorStateProps) {
  return (
    <Card className="rounded-xl">
      <CardContent className="flex flex-col items-center justify-center py-14 text-center">
        <div className="mb-4 flex size-12 items-center justify-center rounded-xl border border-destructive/20 bg-destructive/10">
          <AlertCircle className="size-5 text-destructive" />
        </div>
        <h3 className="mb-1.5 text-base font-semibold">{title}</h3>
        <p className="mb-6 max-w-md text-sm text-muted-foreground">{description}</p>
        {onRetry && (
          <Button onClick={onRetry} variant="outline">
            <RefreshCw className="mr-2 h-4 w-4" />
            Try Again
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
