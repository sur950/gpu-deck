'use client';

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface CopyButtonProps {
  value: string;
  /** Toast text; omit for a silent copy (icon feedback only). */
  successText?: string;
  label?: string;
  size?: 'icon-xs' | 'icon-sm' | 'sm' | 'xs';
  variant?: 'ghost' | 'outline' | 'secondary';
  className?: string;
}

/** Copies `value` to the clipboard and flips to a check mark for a moment. */
export function CopyButton({
  value,
  successText = 'Copied to clipboard',
  label,
  size = 'icon-xs',
  variant = 'ghost',
  className,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (successText) toast.success(successText);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error('Clipboard is not available in this browser');
    }
  }

  const Icon = copied ? Check : Copy;
  return (
    <Button
      type="button"
      variant={variant}
      size={size}
      onClick={copy}
      aria-label={label ?? 'Copy'}
      className={cn('text-muted-foreground hover:text-foreground', copied && 'text-success', className)}
    >
      <Icon className="size-3.5" />
      {label && <span>{copied ? 'Copied' : label}</span>}
    </Button>
  );
}
