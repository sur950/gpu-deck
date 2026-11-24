import { Activity, AlertOctagon, ChartColumn, Hourglass, Layers, Radio, ScrollText, TriangleAlert, Waypoints } from 'lucide-react';
import type { NavEntry } from '@/lib/nav/types';

/** Nav entry: observability dashboards, logs, traces. */
export const observabilityNav: NavEntry[] = [
  {
    id: 'observability-inference',
    title: 'Inference',
    href: '/observability/inference',
    icon: Activity,
    group: 'observability',
    order: 10,
    description: 'Sessions, throughput, time to first token, KV cache per session and cache hit tokens.',
    keywords: ['inference', 'latency', 'ttft', 'throughput', 'kv cache', 'sessions'],
    actions: [{ label: 'Live sessions and KV cache per slot', href: '/observability/inference#sessions', icon: Layers, keywords: ['slots', 'kv', 'sessions', 'heat'] }],
  },
  {
    id: 'observability-infra',
    title: 'GPUs / infra',
    href: '/observability/infra',
    icon: ChartColumn,
    group: 'observability',
    order: 20,
    description: 'Utilisation, VRAM, power, temperature and errors per GPU and node.',
    keywords: ['gpu', 'infra', 'vram', 'power', 'temperature', 'utilisation'],
    detailLabel: 'GPU details',
  },
  {
    id: 'observability-logs',
    title: 'Logs',
    href: '/observability/logs',
    icon: ScrollText,
    group: 'observability',
    order: 30,
    description: 'Engine logs and rule action logs, live or by time range.',
    keywords: ['logs', 'engine', 'rule actions'],
    actions: [
      { label: 'Tail logs live', href: '/observability/logs?mode=live', icon: Radio, keywords: ['tail', 'follow', 'stream'] },
      { label: 'Show warnings and errors', href: '/observability/logs?mode=history&level=warn&range=24h', icon: TriangleAlert, keywords: ['errors', 'warnings'] },
    ],
  },
  {
    id: 'observability-traces',
    title: 'Traces',
    href: '/observability/traces',
    icon: Waypoints,
    group: 'observability',
    order: 40,
    description: 'Request traces through the API layer, agent proxy and engine (OpenTelemetry).',
    keywords: ['trace', 'otel', 'opentelemetry', 'span'],
    detailLabel: 'Trace details',
    actions: [
      { label: 'Find failed requests', href: '/observability/traces?status=errors', icon: AlertOctagon, keywords: ['errors', '5xx', '502'] },
      { label: 'Find slow requests', href: '/observability/traces?min=5000', icon: Hourglass, keywords: ['slow', 'latency'] },
    ],
  },
];
