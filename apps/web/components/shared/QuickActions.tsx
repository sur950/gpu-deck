'use client';

import Link from 'next/link';
import { ArrowRight } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface QuickAction {
  id: string;
  title: string;
  description: string;
  icon: LucideIcon;
  href: string;
}

interface QuickActionsProps {
  actions: QuickAction[];
  heading?: string;
}

/** Link tiles grid (no permission filter — one admin). */
export function QuickActions({ actions, heading = 'Quick actions' }: QuickActionsProps) {
  if (actions.length === 0) return null;

  return (
    <section aria-labelledby="quick-actions-heading">
      <h2 id="quick-actions-heading" className="mb-3 text-sm font-semibold">
        {heading}
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {actions.map((action) => {
          const Icon = action.icon;
          return (
            <Link
              key={action.id}
              href={action.href}
              className="group/action relative flex items-center gap-4 rounded-xl border border-border bg-card p-4 shadow-premium outline-none transition-all duration-200 hover:-translate-y-0.5 hover:border-foreground/15 hover:shadow-premium-lg focus-visible:ring-2 focus-visible:ring-ring/30 active:translate-y-0"
            >
              <span className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-foreground transition-colors duration-200 group-hover/action:border-brand/25 group-hover/action:bg-brand/10 group-hover/action:text-brand">
                <Icon className="size-[18px]" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-sm font-semibold">{action.title}</span>
                <span className="mt-0.5 block text-xs text-muted-foreground">{action.description}</span>
              </span>
              <ArrowRight className="size-4 shrink-0 -translate-x-1 text-muted-foreground opacity-0 transition-all duration-200 group-hover/action:translate-x-0 group-hover/action:opacity-100" />
            </Link>
          );
        })}
      </div>
    </section>
  );
}
