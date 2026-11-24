import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Chart color constants — the validated categorical palette in globals.css
 * (--chart-1 follows the brand accent). Assign in this fixed order;
 * a sixth series is "Other" in neutral grey, never a generated hue.
 */
export const CHART_COLORS = {
  primary: 'rgb(var(--chart-1))',
  secondary: 'rgb(var(--chart-2))',
  tertiary: 'rgb(var(--chart-3))',
  quaternary: 'rgb(var(--chart-4))',
  quinary: 'rgb(var(--chart-5))',
};

export const MODEL_COLORS = [
  'rgb(var(--chart-1))',
  'rgb(var(--chart-2))',
  'rgb(var(--chart-3))',
  'rgb(var(--chart-4))',
  'rgb(var(--chart-5))',
  'rgb(var(--muted-foreground))',
];
