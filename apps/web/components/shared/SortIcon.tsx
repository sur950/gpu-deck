/**
 * Shared SortIcon component
 * Shows sort direction indicators for table columns
 */

import { ArrowUpDown, ArrowUp, ArrowDown } from 'lucide-react';

interface SortIconProps<T extends string> {
  field: T;
  sortField: T;
  sortDirection: 'asc' | 'desc';
}

export function SortIcon<T extends string>({ field, sortField, sortDirection }: SortIconProps<T>) {
  if (sortField !== field) {
    return <ArrowUpDown className="ml-2 h-4 w-4 text-muted-foreground" />;
  }
  return sortDirection === 'asc' ? (
    <ArrowUp className="ml-2 h-4 w-4" />
  ) : (
    <ArrowDown className="ml-2 h-4 w-4" />
  );
}
