'use client';

import { useMemo, useState } from 'react';
import { ArrowDownUp, Cpu, SearchX } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { SearchInput } from '@/components/shared/search-input';
import { EmptyState } from '@/components/shared/EmptyState';
import { useDebounce } from '@/hooks/use-debounce';
import { useCloudInstanceTypes, cloudErrorMessage } from '@/hooks/use-cloud';
import type { CloudAccountRef } from '@/lib/api/cloud';
import { cn } from '@/lib/utils';
import { formatBytes } from '@/utils/format';
import { formatHourly, perGpuHour } from '@/components/cloud/meta';
import type { CloudInstanceType, CloudProviderId, InstanceTypeFilters } from '@/types/cloud';

type SortKey = 'price' | 'gpu_price' | 'vram';

const VRAM_OPTIONS = [0, 16, 24, 48, 80, 160, 320];
const COUNT_OPTIONS = [0, 1, 2, 4, 8];
const PRICE_OPTIONS = [0, 1, 2, 5, 10, 40];

function FilterSelect({ label, value, options, format, onChange }: { label: string; value: number; options: number[]; format: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <Select value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger className="h-9 w-36" aria-label={label}>
        <SelectValue>{(v: unknown) => format(Number(v))}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {options.map((o) => (
          <SelectItem key={o} value={String(o)}>
            {format(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

interface InstanceTypeBrowserProps {
  provider: CloudProviderId;
  region: string;
  account: CloudAccountRef;
  value: string;
  onChange: (type: CloudInstanceType) => void;
}

/**
 * GPU instance types of a region as a spec sheet: GPU, VRAM (bar scaled to
 * the largest in view), vCPU / RAM, and list price both per hour and per
 * GPU-hour, so an 8-GPU box and a 1-GPU box can be compared directly.
 * Filters go to the API (vendor, min VRAM, GPU count, max price, search).
 */
export function InstanceTypeBrowser({ provider, region, account, value, onChange }: InstanceTypeBrowserProps) {
  const [vendor, setVendor] = useState('');
  const [minVram, setMinVram] = useState(0);
  const [count, setCount] = useState(0);
  const [maxPrice, setMaxPrice] = useState(0);
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('gpu_price');
  const debounced = useDebounce(search, 300);
  const filters: InstanceTypeFilters = { gpu_vendor: vendor, min_vram_gb: minVram, gpu_count: count, max_price: maxPrice, search: debounced };
  const { data, isLoading, isError, error, isFetching } = useCloudInstanceTypes(provider, region, account, filters);

  const rows = useMemo(() => {
    const list = [...(data ?? [])];
    const price = (t: CloudInstanceType) => t.price_per_hour_usd ?? Number.POSITIVE_INFINITY;
    const gpuPrice = (t: CloudInstanceType) => perGpuHour(t) ?? Number.POSITIVE_INFINITY;
    list.sort((a, b) => (sort === 'vram' ? (b.total_vram_bytes ?? 0) - (a.total_vram_bytes ?? 0) : sort === 'price' ? price(a) - price(b) : gpuPrice(a) - gpuPrice(b)) || a.id.localeCompare(b.id));
    return list;
  }, [data, sort]);
  const maxVram = Math.max(1, ...rows.map((r) => r.total_vram_bytes ?? 0));
  const cheapestPerGpu = Math.min(...rows.map((r) => perGpuHour(r) ?? Number.POSITIVE_INFINITY));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput value={search} onChange={setSearch} placeholder="Type, GPU model, family…" className="w-56" />
        <Select value={vendor || 'any'} onValueChange={(v) => setVendor(v === 'any' ? '' : String(v))}>
          <SelectTrigger className="h-9 w-32" aria-label="GPU vendor">
            <SelectValue>{(v: unknown) => (v === 'any' ? 'Any vendor' : v === 'nvidia' ? 'NVIDIA' : 'AMD')}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="any">Any vendor</SelectItem>
            <SelectItem value="nvidia">NVIDIA</SelectItem>
            <SelectItem value="amd">AMD</SelectItem>
          </SelectContent>
        </Select>
        <FilterSelect label="Minimum VRAM" value={minVram} options={VRAM_OPTIONS} format={(v) => (v ? `≥ ${v} GiB VRAM` : 'Any VRAM')} onChange={setMinVram} />
        <FilterSelect label="GPU count" value={count} options={COUNT_OPTIONS} format={(v) => (v ? `${v} GPU${v === 1 ? '' : 's'}` : 'Any GPU count')} onChange={setCount} />
        <FilterSelect label="Max price" value={maxPrice} options={PRICE_OPTIONS} format={(v) => (v ? `≤ $${v}/h` : 'Any price')} onChange={setMaxPrice} />
        <Select value={sort} onValueChange={(v) => setSort(v as SortKey)}>
          <SelectTrigger className="ml-auto h-9 w-44" aria-label="Sort">
            <ArrowDownUp className="size-3.5 text-muted-foreground" />
            <SelectValue>{(v: unknown) => ({ gpu_price: 'Cheapest per GPU', price: 'Cheapest per VM', vram: 'Most VRAM' })[v as SortKey]}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="gpu_price">Cheapest per GPU</SelectItem>
            <SelectItem value="price">Cheapest per VM</SelectItem>
            <SelectItem value="vram">Most VRAM</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isLoading ? (
        <div className="space-y-1.5">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12 rounded-md" />)}</div>
      ) : isError ? (
        <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{cloudErrorMessage(error)}</p>
      ) : rows.length === 0 ? (
        <EmptyState icon={SearchX} title="No GPU types match" description={`Nothing in ${region} fits these filters. Loosen them or try another region.`} />
      ) : (
        <div role="radiogroup" aria-label="Instance type" className={cn('overflow-hidden rounded-md border border-border transition-opacity', isFetching && 'opacity-70')}>
          <div className="hidden grid-cols-[minmax(0,1.5fr)_minmax(0,1.4fr)_minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,1.1fr)] gap-3 border-b border-border bg-muted/40 px-3 py-2 text-[11px] font-medium text-muted-foreground md:grid">
            <span>Instance type</span>
            <span>GPU</span>
            <span>VRAM</span>
            <span>vCPU · RAM</span>
            <span className="text-right">List price</span>
          </div>
          <ul className="max-h-[420px] divide-y divide-border overflow-y-auto">
            {rows.map((t) => {
              const selected = t.id === value;
              const gpuHour = perGpuHour(t);
              const best = gpuHour !== null && gpuHour === cheapestPerGpu;
              return (
                <li key={t.id}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => onChange(t)}
                    className={cn(
                      'relative grid w-full cursor-pointer grid-cols-2 gap-x-3 gap-y-1 px-3 py-2.5 text-left outline-none transition-colors md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.4fr)_minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,1.1fr)] md:items-center',
                      'hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:ring-inset',
                      selected && 'bg-brand/[0.06] hover:bg-brand/[0.08]',
                    )}
                  >
                    {selected && <span className="absolute inset-y-1.5 left-0 w-[3px] rounded-r-full bg-brand" aria-hidden />}
                    <span className="flex min-w-0 items-center gap-2.5">
                      <span className={cn('flex size-4 shrink-0 items-center justify-center rounded-full border', selected ? 'border-foreground' : 'border-border')} aria-hidden>
                        {selected && <span className="size-2 rounded-full bg-foreground" />}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate font-mono text-[12.5px] font-medium">{t.id}</span>
                        <span className="block truncate text-[11px] text-muted-foreground">{[t.family, (t.extra?.architectures as string[] | undefined)?.join('/'), t.extra?.partial_gpu ? 'partial GPU' : null].filter(Boolean).join(' · ')}</span>
                      </span>
                    </span>
                    <span className="flex min-w-0 items-center gap-1.5 text-[13px]">
                      <Cpu className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      <span className="shrink-0 font-medium whitespace-nowrap tabular-nums">{t.gpu_count} ×</span>
                      <span className="truncate" title={t.gpu_model ?? undefined}>
                        {t.gpu_model ?? 'GPU'}
                      </span>
                    </span>
                    <span className="space-y-1">
                      <span className="block font-mono text-xs whitespace-nowrap tabular-nums" title={t.gpu_count > 1 && t.gpu_vram_bytes ? `${formatBytes(t.gpu_vram_bytes)} per GPU` : undefined}>
                        {t.total_vram_bytes ? formatBytes(t.total_vram_bytes) : '—'}
                      </span>
                      <span className="block h-1 overflow-hidden rounded-full bg-muted">
                        <span className="block h-full rounded-full bg-[rgb(var(--chart-2))]" style={{ width: `${((t.total_vram_bytes ?? 0) / maxVram) * 100}%` }} />
                      </span>
                    </span>
                    <span className="font-mono text-xs whitespace-nowrap tabular-nums text-muted-foreground">
                      {t.vcpus ?? '—'} · {t.memory_bytes ? formatBytes(t.memory_bytes) : '—'}
                    </span>
                    <span className="text-right">
                      <span className="block font-mono text-[13px] font-semibold tabular-nums">{formatHourly(t.price_per_hour_usd)}</span>
                      <span className={cn('block text-[11px] whitespace-nowrap tabular-nums', best ? 'font-medium text-success-dark dark:text-success-light' : 'text-muted-foreground')}>
                        {gpuHour !== null ? `${formatHourly(gpuHour).replace('/h', '')}/GPU-h${best ? ' · best' : ''}` : 'no list price'}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">On-demand list prices from the provider API where it publishes them; your contract price may differ. GCP lists no prices.</p>
    </div>
  );
}
