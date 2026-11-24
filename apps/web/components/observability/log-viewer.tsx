'use client';

import { Fragment, useCallback, useEffect, useLayoutEffect, useRef } from 'react';
import { ArrowDown, ScrollText } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/EmptyState';
import { LOG_SOURCE_LABELS } from '@/types/logs';
import type { LogChunk, LogLevel } from '@/types/logs';

const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const DAY = new Intl.DateTimeFormat('en-US', { month: 'short', day: '2-digit' });

export const LEVEL_STYLE: Record<LogLevel, { label: string; tag: string; row: string }> = {
  debug: { label: 'DEBUG', tag: 'text-muted-foreground', row: 'text-muted-foreground' },
  info: { label: 'INFO', tag: 'text-info-dark dark:text-info-light', row: '' },
  warn: { label: 'WARN', tag: 'text-warning-dark dark:text-warning-light', row: 'bg-warning/[0.06]' },
  error: { label: 'ERROR', tag: 'text-error-dark dark:text-error-light', row: 'bg-error/[0.07]' },
};

function stamp(iso: string, withDay: boolean): string {
  const d = new Date(iso);
  const ms = String(d.getMilliseconds()).padStart(3, '0');
  return `${withDay ? `${DAY.format(d)} ` : ''}${TIME.format(d)}.${ms}`;
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const parts: React.ReactNode[] = [];
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  let i = 0;
  let k = 0;
  while (i < text.length) {
    const hit = lower.indexOf(q, i);
    if (hit < 0) {
      parts.push(<Fragment key={k++}>{text.slice(i)}</Fragment>);
      break;
    }
    if (hit > i) parts.push(<Fragment key={k++}>{text.slice(i, hit)}</Fragment>);
    parts.push(
      <mark key={k++} className="rounded-[2px] bg-warning/30 px-px text-inherit">
        {text.slice(hit, hit + q.length)}
      </mark>,
    );
    i = hit + q.length;
  }
  return <>{parts}</>;
}

interface LogViewerProps {
  chunks: LogChunk[];
  /** Keep the newest line in view (tail -f). */
  follow: boolean;
  onFollowChange?: (follow: boolean) => void;
  /** Newest at the bottom (tail) or at the top (search results). */
  order: 'asc' | 'desc';
  search?: string;
  showDay?: boolean;
  nodeNames?: Map<string, string>;
  onRequestClick?: (requestId: string) => void;
  footer?: React.ReactNode;
  emptyTitle?: string;
  emptyDescription?: string;
  className?: string;
}

/**
 * Dense monospace log list. Each chunk keeps its own lines; the level tag is
 * a word (not only a colour), warn / error rows get a faint wash. In follow
 * mode the view sticks to the newest line until the reader scrolls up, then
 * offers "Jump to latest".
 */
export function LogViewer({
  chunks,
  follow,
  onFollowChange,
  order,
  search = '',
  showDay = false,
  nodeNames,
  onRequestClick,
  footer,
  emptyTitle = 'No log lines',
  emptyDescription = 'Nothing matches these filters yet.',
  className,
}: LogViewerProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const programmatic = useRef(false);

  const scrollToLatest = useCallback(() => {
    const el = scroller.current;
    if (!el) return;
    programmatic.current = true;
    el.scrollTop = order === 'asc' ? el.scrollHeight : 0;
  }, [order]);

  // Stick to the newest line while following.
  useLayoutEffect(() => {
    if (follow) scrollToLatest();
  }, [chunks, follow, scrollToLatest]);

  useEffect(() => {
    const el = scroller.current;
    if (!el || !onFollowChange) return;
    const onScroll = () => {
      if (programmatic.current) {
        programmatic.current = false;
        return;
      }
      const atLatest = order === 'asc' ? el.scrollHeight - el.scrollTop - el.clientHeight < 32 : el.scrollTop < 32;
      onFollowChange(atLatest);
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, [onFollowChange, order]);

  return (
    <div className={cn('relative', className)}>
      <div
        ref={scroller}
        role="log"
        aria-live={follow ? 'polite' : 'off'}
        aria-relevant="additions"
        className="h-full overflow-auto bg-muted/20 py-1 text-[12px] leading-[1.6] dark:bg-black/20 font-mono"
      >
        {chunks.length === 0 ? (
          <div className="flex h-full items-center justify-center font-sans">
            <EmptyState icon={ScrollText} title={emptyTitle} description={emptyDescription} />
          </div>
        ) : (
          <>
            {chunks.map((chunk) => {
              const lvl = LEVEL_STYLE[chunk.level];
              const node = chunk.nodeName ?? (chunk.nodeId ? nodeNames?.get(chunk.nodeId) : null);
              return (
                <div
                  key={`${chunk.id}-${chunk.ts}`}
                  className={cn(
                    'group/log grid grid-cols-[auto_3.4rem_minmax(0,1fr)] gap-x-3 border-l-2 border-transparent px-4 py-0.5 hover:bg-accent/40',
                    lvl.row,
                    chunk.level === 'error' && 'border-error/70',
                    chunk.level === 'warn' && 'border-warning/60',
                  )}
                >
                  <time dateTime={chunk.ts} className="whitespace-nowrap text-muted-foreground tabular-nums">
                    {stamp(chunk.ts, showDay)}
                  </time>
                  <span className={cn('font-semibold', lvl.tag)}>{lvl.label}</span>
                  <div className="min-w-0">
                    <span className="mr-2 text-muted-foreground">
                      [{LOG_SOURCE_LABELS[chunk.source]}
                      {node ? ` · ${node}` : ''}
                      {chunk.logger ? ` · ${chunk.logger}` : ''}]
                    </span>
                    <span className={cn('whitespace-pre-wrap break-words', chunk.level === 'debug' ? 'text-muted-foreground' : 'text-foreground')}>
                      <Highlight text={chunk.content} query={search} />
                    </span>
                    {chunk.requestId && onRequestClick && (
                      <button
                        type="button"
                        onClick={() => onRequestClick(chunk.requestId as string)}
                        className="ml-2 rounded-sm text-[11px] text-muted-foreground underline-offset-2 opacity-60 outline-none transition-opacity hover:text-foreground hover:underline focus-visible:opacity-100 group-hover/log:opacity-100"
                        title="Show every line for this request"
                      >
                        req {chunk.requestId.slice(0, 8)}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
            {footer}
          </>
        )}
      </div>

      {!follow && onFollowChange && chunks.length > 0 && (
        <Button
          type="button"
          size="sm"
          onClick={() => {
            onFollowChange(true);
            scrollToLatest();
          }}
          className={cn('absolute right-4 h-8 gap-1.5 rounded-full px-3 font-sans shadow-premium-lg', order === 'asc' ? 'bottom-4' : 'top-4')}
        >
          <ArrowDown className={cn('size-3.5', order === 'desc' && 'rotate-180')} />
          Jump to latest
        </Button>
      )}
    </div>
  );
}
