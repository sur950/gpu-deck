'use client';

import { useState } from 'react';
import { Globe, LogOut, Monitor, Terminal } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { ConfirmationDialog } from '@/components/shared/ConfirmationDialog';
import { ErrorState } from '@/components/shared/ErrorState';
import { RelativeTime } from '@/components/shared/relative-time';
import { useAdminSessions, useLogoutOtherSessions, useRevokeSession } from '@/hooks/use-account';
import type { AdminSession } from '@/types/account';

/** "in 9 h", "in 40 min" — the shared relative formatter only does the past. */
function expiresIn(iso: string): string {
  const ms = new Date(iso).getTime() - Date.now();
  if (ms <= 0) return 'expired';
  const min = Math.round(ms / 60_000);
  if (min < 60) return `expires in ${min} min`;
  const h = Math.round(min / 60);
  if (h < 48) return `expires in ${h} h`;
  return `expires in ${Math.round(h / 24)} days`;
}

/** "Safari on macOS", "GPU Deck CLI 0.1.0", "Terraform provider 0.1.0". */
export function describeAgent(ua: string | null): { label: string; icon: LucideIcon } {
  if (!ua) return { label: 'Unknown client', icon: Globe };
  const cli = ua.match(/gpudeck-cli\/([\w.]+)/);
  if (cli) return { label: `GPU Deck CLI ${cli[1]}`, icon: Terminal };
  const tf = ua.match(/terraform-provider-gpudeck\/([\w.]+)/);
  if (tf) return { label: `Terraform provider ${tf[1]}`, icon: Terminal };
  const browser = /Edg\//.test(ua) ? 'Edge' : /Chrome\//.test(ua) ? 'Chrome' : /Firefox\//.test(ua) ? 'Firefox' : /Safari\//.test(ua) ? 'Safari' : null;
  const os = /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Linux/.test(ua) ? 'Linux' : null;
  if (browser) return { label: `${browser}${os ? ` on ${os}` : ''}`, icon: Monitor };
  return { label: ua.split(' ')[0].slice(0, 40), icon: Globe };
}

/** Active sessions of the admin, with sign-out per session and for all others. */
export function SessionsCard() {
  const sessions = useAdminSessions();
  const revoke = useRevokeSession();
  const logoutOthers = useLogoutOtherSessions();
  const [target, setTarget] = useState<AdminSession | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const list = [...(sessions.data ?? [])].sort((a, b) => Number(b.isCurrent) - Number(a.isCurrent) || (b.lastActiveAt ?? '').localeCompare(a.lastActiveAt ?? ''));
  const others = list.filter((s) => !s.isCurrent).length;

  return (
    <Card id="sessions" className="scroll-mt-20 rounded-md shadow-premium">
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div className="space-y-1">
          <CardTitle>Active sessions</CardTitle>
          <CardDescription>Browsers, the CLI and Terraform signed in as this admin. Changing the password signs out all of them except this one.</CardDescription>
        </div>
        <Button variant="outline" size="sm" className="shrink-0 gap-1.5 rounded-sm" disabled={others === 0 || logoutOthers.isPending} onClick={() => setConfirmAll(true)}>
          <LogOut className="size-3.5" />
          Sign out others
        </Button>
      </CardHeader>
      <CardContent>
        {sessions.isPending ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-14 rounded-lg" />
            ))}
          </div>
        ) : sessions.isError ? (
          <ErrorState title="Couldn't load sessions" onRetry={() => void sessions.refetch()} />
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {list.map((s) => {
              const agent = describeAgent(s.userAgent);
              const Icon = agent.icon;
              return (
                <li key={s.id} className="flex items-center gap-3 px-3 py-3">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-border bg-muted/60 text-muted-foreground">
                    <Icon className="size-4" aria-hidden />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-2 text-sm font-medium">
                      <span className="truncate">{agent.label}</span>
                      {s.isCurrent && (
                        <span className="shrink-0 rounded-full border border-success/30 bg-success/10 px-1.5 py-px text-[10px] font-semibold text-success-dark dark:text-success-light">This session</span>
                      )}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      <span className="text-[11px] font-mono">{s.ipAddress ?? 'unknown IP'}</span> · active <RelativeTime value={s.lastActiveAt ?? s.createdAt} /> · signed in{' '}
                      <RelativeTime value={s.createdAt} /> · <span title={new Date(s.expiresAt).toLocaleString()}>{expiresIn(s.expiresAt)}</span>
                    </p>
                  </div>
                  {!s.isCurrent && (
                    <Button variant="ghost" size="sm" className="shrink-0 rounded-sm text-muted-foreground hover:text-destructive" onClick={() => setTarget(s)}>
                      Sign out
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <ConfirmationDialog
        open={target !== null}
        onOpenChange={(open) => !open && setTarget(null)}
        title="Sign out this session?"
        description={target ? `${describeAgent(target.userAgent).label} from ${target.ipAddress ?? 'an unknown IP'} will have to sign in again.` : ''}
        confirmText="Sign out"
        variant="destructive"
        isLoading={revoke.isPending}
        onConfirm={() => {
          if (!target) return;
          revoke.mutate(target.id, { onSettled: () => setTarget(null) });
        }}
      />
      <ConfirmationDialog
        open={confirmAll}
        onOpenChange={setConfirmAll}
        title="Sign out every other session?"
        description={`${others} other session${others === 1 ? '' : 's'} (browsers, CLI, Terraform) will have to sign in again. This one stays signed in.`}
        confirmText="Sign out others"
        variant="destructive"
        isLoading={logoutOthers.isPending}
        onConfirm={() => logoutOthers.mutate(undefined, { onSettled: () => setConfirmAll(false) })}
      />
    </Card>
  );
}
