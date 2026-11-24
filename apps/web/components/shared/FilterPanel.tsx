/**
 * Reusable Filter Panel component
 * Collapsible panel for advanced filtering options
 */

'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Filter, X, ChevronDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';

interface FilterPanelProps {
  children: React.ReactNode;
  onReset?: () => void;
  onApply?: () => void;
  isExpanded?: boolean;
  onToggle?: (expanded: boolean) => void;
}

export function FilterPanel({
  children,
  onReset,
  onApply,
  isExpanded: controlledExpanded,
  onToggle,
}: FilterPanelProps) {
  const [internalExpanded, setInternalExpanded] = useState(false);

  const isExpanded = controlledExpanded !== undefined ? controlledExpanded : internalExpanded;

  const handleToggle = () => {
    const newValue = !isExpanded;
    if (onToggle) {
      onToggle(newValue);
    } else {
      setInternalExpanded(newValue);
    }
  };

  return (
    <div className="border rounded-lg bg-card shadow-premium">
      <div className="flex items-center justify-between p-4 border-b">
        <Button
          variant="ghost"
          onClick={handleToggle}
          className="h-auto p-0 flex items-center gap-2 font-semibold text-sm hover:text-foreground transition-colors"
        >
          <Filter className="h-4 w-4" />
          <span>Filters</span>
          {isExpanded ? (
            <ChevronUp className="h-4 w-4" />
          ) : (
            <ChevronDown className="h-4 w-4" />
          )}
        </Button>
        {onReset && (
          <Button variant="ghost" size="sm" onClick={onReset}>
            <X className="mr-2 h-4 w-4" />
            Reset
          </Button>
        )}
      </div>

      {isExpanded && (
        <div className="p-4">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {children}
          </div>
          {onApply && (
            <div className="mt-4 flex justify-end">
              <Button onClick={onApply}>Apply Filters</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

interface FilterFieldProps {
  label: string;
  children: React.ReactNode;
  className?: string;
}

export function FilterField({ label, children, className }: FilterFieldProps) {
  return (
    <div className={cn('space-y-2', className)}>
      <Label className="text-sm font-medium">{label}</Label>
      {children}
    </div>
  );
}
