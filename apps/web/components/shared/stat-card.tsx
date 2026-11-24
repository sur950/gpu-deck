import type { LucideIcon } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  icon: LucideIcon;
  /** Muted line under the value. */
  footnote?: React.ReactNode;
  className?: string;
}

/** The list-page stat tile (`Card rounded-md shadow-premium`, label row + icon, bold value). */
export function StatCard({ label, value, icon: Icon, footnote, className }: StatCardProps) {
  return (
    <Card className={cn('rounded-md shadow-premium', className)}>
      <CardContent>
        <div className="mb-2 flex items-center justify-between">
          <p className="text-sm text-muted-foreground">{label}</p>
          <Icon className="h-4 w-4 text-muted-foreground" />
        </div>
        <div className="text-2xl font-bold tabular-nums">{value}</div>
        {footnote && <p className="mt-1 text-xs text-muted-foreground">{footnote}</p>}
      </CardContent>
    </Card>
  );
}
