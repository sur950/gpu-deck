import { AudioLines, Brain, Eye, Film, Image as ImageIcon, ListOrdered, MessageSquare, Mic, Shield, ShieldAlert, Sigma } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import {
  ENGINE_LABELS,
  MODEL_FORMAT_LABELS,
  MODEL_PRIORITY_DESCRIPTIONS,
  MODEL_PRIORITY_LABELS,
  MODEL_SOURCE_LABELS,
  MODEL_TYPE_LABELS,
  type EngineName,
  type ModelFileFormat,
  type ModelPriority,
  type ModelSource,
  type ModelType,
} from '@/types/models';

export const MODEL_TYPE_ICONS: Record<ModelType, LucideIcon> = {
  chat: MessageSquare,
  thinking: Brain,
  vision: Eye,
  embedding: Sigma,
  rerank: ListOrdered,
  stt: Mic,
  tts: AudioLines,
  image: ImageIcon,
  video: Film,
};

export function ModelTypeBadge({ type, className }: { type: ModelType; className?: string }) {
  const Icon = MODEL_TYPE_ICONS[type];
  return (
    <Badge variant="outline" className={cn('gap-1 font-normal', className)}>
      <Icon className="size-3 text-muted-foreground" />
      {MODEL_TYPE_LABELS[type]}
    </Badge>
  );
}

const PRIORITY_STYLE: Record<ModelPriority, string> = {
  always_on: 'border-brand/40 bg-brand/10 text-foreground',
  normal: 'border-border bg-muted/50 text-muted-foreground',
  evictable: 'border-dashed border-border bg-transparent text-muted-foreground',
};

const PRIORITY_DOT: Record<ModelPriority, string> = {
  always_on: 'bg-brand',
  normal: 'bg-muted-foreground/60',
  evictable: 'border border-muted-foreground/60 bg-transparent',
};

/** Model priority pill — "always on" carries the brand accent, "evictable" is dashed. */
export function PriorityBadge({ priority, className }: { priority: ModelPriority; className?: string }) {
  return (
    <span
      title={MODEL_PRIORITY_DESCRIPTIONS[priority]}
      className={cn('inline-flex h-5 items-center gap-1.5 rounded-sm border px-1.5 text-[11px] font-medium whitespace-nowrap', PRIORITY_STYLE[priority], className)}
    >
      <span className={cn('size-1.5 rounded-full', PRIORITY_DOT[priority])} aria-hidden />
      {MODEL_PRIORITY_LABELS[priority]}
    </span>
  );
}

const SOURCE_MARK: Record<ModelSource, string> = { huggingface: 'HF', modelscope: 'MS', local_path: '~/' };

export function SourceBadge({ source, className, withLabel = true }: { source: ModelSource; className?: string; withLabel?: boolean }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs', className)} title={MODEL_SOURCE_LABELS[source]}>
      <span className="flex h-5 min-w-6 items-center justify-center rounded-sm border border-border bg-muted/60 px-1 font-mono text-[9.5px] font-bold text-muted-foreground">{SOURCE_MARK[source]}</span>
      {withLabel && <span>{MODEL_SOURCE_LABELS[source]}</span>}
    </span>
  );
}

const FORMAT_STYLE: Record<ModelFileFormat, string> = {
  gguf: 'text-chart-2 border-chart-2/40',
  safetensors: 'text-chart-3 border-chart-3/40',
  mlx: 'text-chart-4 border-chart-4/40',
  pickle: 'text-error border-error/40 bg-error/5',
  other: 'text-muted-foreground border-border',
};

export function FormatBadge({ format, className }: { format: ModelFileFormat; className?: string }) {
  return (
    <span className={cn('inline-flex h-5 items-center rounded-sm border px-1.5 font-mono text-[10px] font-semibold uppercase tracking-wide', FORMAT_STYLE[format], className)}>
      {MODEL_FORMAT_LABELS[format]}
    </span>
  );
}

export function EngineBadge({ engine, version, className }: { engine: EngineName | null | undefined; version?: string | null; className?: string }) {
  if (!engine) return <span className="text-xs text-muted-foreground">auto</span>;
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs', className)}>
      <span className="font-medium">{ENGINE_LABELS[engine]}</span>
      {version && <span className="font-mono text-[11px] text-muted-foreground">{version}</span>}
    </span>
  );
}

/** trust_remote_code / allow_pickle flags as compact security chips. */
export function SecurityFlags({ trustRemoteCode, allowPickle, className }: { trustRemoteCode: boolean; allowPickle: boolean; className?: string }) {
  if (!trustRemoteCode && !allowPickle) {
    return (
      <span className={cn('inline-flex items-center gap-1 text-[11px] text-success-dark dark:text-success-light', className)} title="trust_remote_code off · pickle blocked">
        <Shield className="size-3.5" />
        Safe defaults
      </span>
    );
  }
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1', className)}>
      {trustRemoteCode && (
        <span className="inline-flex h-5 items-center gap-1 rounded-sm border border-warning/40 bg-warning/10 px-1.5 text-[11px] font-medium text-warning-dark dark:text-warning-light">
          <ShieldAlert className="size-3" />
          remote code
        </span>
      )}
      {allowPickle && (
        <span className="inline-flex h-5 items-center gap-1 rounded-sm border border-error/40 bg-error/10 px-1.5 text-[11px] font-medium text-error-dark dark:text-error-light">
          <ShieldAlert className="size-3" />
          pickle allowed
        </span>
      )}
    </span>
  );
}
