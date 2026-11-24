'use client';

import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';

interface FormDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  children: React.ReactNode;
  submitLabel: string;
  onSubmit: () => void;
  isPending?: boolean;
  submitDisabled?: boolean;
  variant?: 'default' | 'destructive';
  /** Tailwind max-width for the panel (default sm:max-w-lg). */
  widthClass?: string;
  /** Extra content on the left of the footer (hints, totals). */
  footerNote?: React.ReactNode;
}

/**
 * A dialog that collects a few inputs before an action (drain with a reason,
 * scale to N replicas, prefetch to nodes…). Confirm-only prompts keep using
 * `ConfirmationDialog` / `DeleteConfirmationDialog`.
 */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  icon,
  children,
  submitLabel,
  onSubmit,
  isPending = false,
  submitDisabled = false,
  variant = 'default',
  widthClass = 'sm:max-w-lg',
  footerNote,
}: FormDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => !isPending && onOpenChange(next)}>
      <DialogContent className={cn('gap-5 p-5', widthClass)}>
        <DialogHeader className="gap-1.5 pr-8">
          <DialogTitle className="flex items-center gap-2 text-base font-semibold">
            {icon}
            {title}
          </DialogTitle>
          {description && <DialogDescription className="text-[13px] leading-relaxed">{description}</DialogDescription>}
        </DialogHeader>
        <form
          className="space-y-5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!submitDisabled && !isPending) onSubmit();
          }}
        >
          <div className="space-y-4">{children}</div>
          <DialogFooter className="items-center gap-2 sm:justify-between">
            <div className="text-[11px] text-muted-foreground">{footerNote}</div>
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button type="button" variant="outline" size="sm" className="rounded-sm" onClick={() => onOpenChange(false)} disabled={isPending}>
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                className={cn('rounded-sm', variant === 'destructive' && 'bg-destructive text-destructive-foreground hover:bg-destructive/90')}
                disabled={submitDisabled || isPending}
              >
                {isPending && <Loader2 className="size-4 animate-spin" />}
                {submitLabel}
              </Button>
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Label + control + hint row used inside form dialogs and wizard steps. */
export function Field({ label, hint, htmlFor, children, className, aside }: { label: React.ReactNode; hint?: React.ReactNode; htmlFor?: string; children: React.ReactNode; className?: string; aside?: React.ReactNode }) {
  return (
    <div className={cn('space-y-1.5', className)}>
      <div className="flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="text-xs font-medium text-foreground">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}
