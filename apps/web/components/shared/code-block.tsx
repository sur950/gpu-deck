'use client';

import { CopyButton } from '@/components/shared/copy-button';
import { cn } from '@/lib/utils';

interface CodeBlockProps {
  code: string;
  /** Short caption above the block, e.g. a file name or step label. */
  title?: string;
  language?: string;
  className?: string;
  /** Collapse long snippets to a scrollable box. */
  maxHeight?: number;
}

/**
 * Mono code snippet with a copy button in the corner — install commands,
 * curl examples, PEM blocks. Uses the inverse surface so it reads as a
 * terminal in both colour modes.
 */
export function CodeBlock({ code, title, language, className, maxHeight }: CodeBlockProps) {
  return (
    <div className={cn('overflow-hidden rounded-lg border border-inverse-border bg-inverse text-inverse-foreground', className)}>
      {(title || language) && (
        <div className="flex items-center justify-between border-b border-inverse-border px-3 py-1.5">
          <span className="text-[11px] font-medium text-inverse-muted-foreground">{title}</span>
          <span className="text-[11px] uppercase tracking-wide text-inverse-muted-foreground">{language}</span>
        </div>
      )}
      <div className="relative">
        <pre
          className="overflow-x-auto px-3 py-2.5 pr-10 font-mono text-[12px] leading-relaxed whitespace-pre"
          style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
        >
          <code className="font-mono">{code}</code>
        </pre>
        <CopyButton
          value={code}
          className="absolute top-1.5 right-1.5 text-inverse-muted-foreground hover:bg-inverse-foreground/10 hover:text-inverse-foreground"
        />
      </div>
    </div>
  );
}
