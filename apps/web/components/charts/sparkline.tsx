'use client';

import { useId } from 'react';
import { Area, AreaChart, ResponsiveContainer } from 'recharts';

interface SparklineProps {
  values: number[];
}

/**
 * Decorative trend line for a metric card. The values it draws are in the
 * "Usage over time" chart below (with tooltips and a table), so it is hidden
 * from assistive tech rather than duplicated.
 */
export function Sparkline({ values }: SparklineProps) {
  // useId output contains characters that are not safe inside url(#…).
  const gradientId = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`;
  if (values.length < 2) return null;

  const data = values.map((value, index) => ({ index, value }));

  return (
    <div aria-hidden className="h-10 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="rgb(var(--chart-1))" stopOpacity={0.16} />
              <stop offset="100%" stopColor="rgb(var(--chart-1))" stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="value"
            stroke="rgb(var(--chart-1))"
            strokeWidth={1.5}
            fill={`url(#${gradientId})`}
            isAnimationActive
            animationDuration={700}
            dot={false}
            activeDot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
