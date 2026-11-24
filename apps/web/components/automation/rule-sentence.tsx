'use client';

import { cn } from '@/lib/utils';
import type { SentencePart } from '@/components/automation/rule-meta';

interface RuleSentenceProps {
  parts: SentencePart[];
  className?: string;
  /** Field the user is editing — its token gets the brand underline. */
  activeField?: string | null;
  /** Click a token to jump to its input (builder only). */
  onFieldClick?: (field: string) => void;
  size?: 'sm' | 'md' | 'lg';
}

const TONE: Record<string, string> = {
  default: 'bg-foreground/[0.07] text-foreground ring-transparent',
  muted: 'bg-transparent text-foreground/85 underline decoration-dotted decoration-foreground/30 underline-offset-4 ring-transparent',
  warn: 'bg-warning/12 text-warning-dark ring-warning/30 dark:text-warning-light',
};

/**
 * The rule as one readable sentence: prose in muted ink, the values that
 * come from the form as quiet inset tokens. Tokens are buttons in the
 * builder so the sentence doubles as navigation.
 */
export function RuleSentence({ parts, className, activeField, onFieldClick, size = 'md' }: RuleSentenceProps) {
  return (
    <p
      className={cn(
        'text-pretty text-muted-foreground',
        size === 'lg' && 'text-[15px] leading-8',
        size === 'md' && 'text-[13px] leading-7',
        size === 'sm' && 'text-xs leading-6',
        className,
      )}
    >
      {parts.map((part, i) => {
        if (typeof part === 'string') return <span key={i}>{part}</span>;
        const active = !!part.field && part.field === activeField;
        const cls = cn(
          'inline rounded-[4px] px-1 py-px font-medium tabular-nums ring-1 ring-inset transition-colors [box-decoration-break:clone]',
          TONE[part.tone ?? 'default'],
          active && 'bg-brand/10 text-foreground ring-brand/40',
        );
        if (onFieldClick && part.field) {
          const field = part.field;
          return (
            <button
              key={i}
              type="button"
              onClick={() => onFieldClick(field)}
              className={cn(cls, 'cursor-pointer outline-none hover:ring-foreground/30 focus-visible:ring-2 focus-visible:ring-ring/40')}
            >
              {part.value}
            </button>
          );
        }
        return (
          <span key={i} className={cls}>
            {part.value}
          </span>
        );
      })}
    </p>
  );
}
