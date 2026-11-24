/**
 * Date Range Picker component
 * Allows selecting preset ranges or custom date range
 */

'use client';

import { useState } from 'react';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';
import { Calendar } from 'lucide-react';
import { formatDate } from '@/utils/format';

export type DateRangePreset = '24h' | '7d' | '30d' | '90d' | 'custom';

export interface DateRange {
  start: Date;
  end: Date;
}

interface DateRangePickerProps {
  value: DateRange;
  onChange: (range: DateRange) => void;
  presets?: DateRangePreset[];
}

const presetLabels: Record<DateRangePreset, string> = {
  '24h': 'Last 24 Hours',
  '7d': 'Last 7 Days',
  '30d': 'Last 30 Days',
  '90d': 'Last 90 Days',
  'custom': 'Custom Range',
};

export function DateRangePicker({
  value,
  onChange,
  presets = ['24h', '7d', '30d', '90d'],
}: DateRangePickerProps) {
  const [selectedPreset, setSelectedPreset] = useState<DateRangePreset>('30d');

  const getPresetRange = (preset: DateRangePreset): DateRange => {
    const end = new Date();
    const start = new Date();

    switch (preset) {
      case '24h':
        start.setHours(start.getHours() - 24);
        break;
      case '7d':
        start.setDate(start.getDate() - 7);
        break;
      case '30d':
        start.setDate(start.getDate() - 30);
        break;
      case '90d':
        start.setDate(start.getDate() - 90);
        break;
      default:
        break;
    }

    return { start, end };
  };

  const handlePresetSelect = (preset: DateRangePreset) => {
    setSelectedPreset(preset);
    if (preset !== 'custom') {
      onChange(getPresetRange(preset));
    }
  };

  const displayText =
    selectedPreset === 'custom'
      ? `${formatDate(value.start, 'short')} - ${formatDate(value.end, 'short')}`
      : presetLabels[selectedPreset];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="inline-flex items-center justify-center gap-2 h-10 px-4 rounded-lg border-2 border-input bg-background hover:bg-muted hover:text-foreground transition-colors outline-none">
        <Calendar className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">{displayText}</span>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        {presets.map((preset) => (
          <DropdownMenuItem
            key={preset}
            onClick={() => handlePresetSelect(preset)}
            className={selectedPreset === preset ? 'bg-muted' : ''}
          >
            {presetLabels[preset]}
          </DropdownMenuItem>
        ))}
        {presets.includes('custom') && (
          <>
            <DropdownMenuSeparator />
            <div className="p-2 text-xs text-muted-foreground">
              Custom date picker coming soon
            </div>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
