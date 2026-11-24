'use client';

import { KeyRound, ShieldCheck } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { DetailList } from '@/components/shared/detail-list';
import { RelativeTime } from '@/components/shared/relative-time';
import { useProfile } from '@/hooks/use-account';
import { formatDateTime } from '@/utils/format';

function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  return (parts.length >= 2 ? `${parts[0][0]}${parts[1][0]}` : name.slice(0, 2)).toUpperCase();
}

/** Who is signed in and when the password last changed. */
export function ProfileCard() {
  const profile = useProfile();
  const p = profile.data;
  return (
    <Card className="rounded-md shadow-premium">
      <CardHeader>
        <CardTitle>Admin</CardTitle>
        <CardDescription>The single seeded administrator of this control plane.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!p ? (
          <div className="space-y-2">
            <Skeleton className="h-12 rounded-lg" />
            <Skeleton className="h-28 rounded-lg" />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-3">
              <Avatar className="size-11 bg-muted ring-1 ring-border">
                <AvatarFallback className="text-sm font-semibold">{initials(p.displayName || p.username)}</AvatarFallback>
              </Avatar>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold">{p.displayName || p.username}</p>
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="font-mono">{p.username}</span>
                  <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted/60 px-1.5 py-px text-[10px] font-semibold">
                    <ShieldCheck className="size-3" /> Admin
                  </span>
                </p>
              </div>
            </div>
            <DetailList
              items={[
                { label: 'Last sign-in', value: p.lastLoginAt ? <span title={formatDateTime(p.lastLoginAt)}><RelativeTime value={p.lastLoginAt} /></span> : null },
                { label: 'From', value: p.lastLoginIp ? <span className="text-[11px] font-mono">{p.lastLoginIp}</span> : null },
                { label: 'Password changed', value: p.passwordChangedAt ? <RelativeTime value={p.passwordChangedAt} /> : 'never' },
                { label: 'Account created', value: p.createdAt ? formatDateTime(p.createdAt) : null },
              ]}
            />
          </>
        )}
        <div className="flex gap-2.5 rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
          <KeyRound className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          <p>
            The admin is seeded from <span className="text-foreground font-mono">ADMIN_USERNAME</span> /{' '}
            <span className="text-foreground font-mono">ADMIN_PASSWORD</span> in <span className="font-mono">.env</span>. A password set here survives restarts; changing{' '}
            <span className="font-mono">ADMIN_PASSWORD</span> in .env and restarting replaces it.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
