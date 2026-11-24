'use client';

import { Loader2, ShieldCheck } from 'lucide-react';
import { CopyButton } from '@/components/shared/copy-button';
import { cn } from '@/lib/utils';
import type { GpuInstance } from '@/types/gpu-instances';

const COPY_ON_INVERSE = 'text-inverse-muted-foreground hover:bg-inverse-foreground/10 hover:text-inverse-foreground';

/**
 * The connection card as a terminal: the `ssh` command on a prompt line, the
 * host key the agent reported and a ready-made known_hosts line, so the
 * first connection can be verified instead of trusted blindly.
 */
export function SshTerminal({ instance, className }: { instance: GpuInstance; className?: string }) {
  const c = instance.connection;
  const command = c.ssh_command;
  const waiting = !c.ready;
  // Agents that report only the fingerprint (no public key) still get it shown; the known_hosts
  // line needs the key itself and is left out then.
  const hostKey = c.host_keys[0] ?? (c.host_key_fingerprint ? { type: null, fingerprint: c.host_key_fingerprint, public_key: null } : undefined);

  return (
    <div className={cn('overflow-hidden rounded-lg border border-inverse-border bg-inverse text-inverse-foreground', className)}>
      <div className="flex items-center gap-3 border-b border-inverse-border px-3 py-2">
        <span className="flex gap-1.5" aria-hidden>
          <span className="size-2.5 rounded-full bg-inverse-foreground/20" />
          <span className="size-2.5 rounded-full bg-inverse-foreground/20" />
          <span className="size-2.5 rounded-full bg-inverse-foreground/20" />
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-inverse-muted-foreground">
          {c.host ? `${c.user}@${c.host}${c.port ? `:${c.port}` : ''}` : `${c.user}@…`}
        </span>
        <span className={cn('inline-flex items-center gap-1.5 text-[11px] font-medium', waiting ? 'text-inverse-muted-foreground' : 'text-success-light')}>
          <span className={cn('size-1.5 rounded-full', waiting ? 'bg-inverse-muted-foreground' : 'bg-success-light')} aria-hidden />
          {waiting ? (instance.state === 'stopped' || instance.state === 'error' ? 'not running' : 'waiting for sshd') : 'ready'}
        </span>
      </div>

      <div className="space-y-3 px-4 py-3.5 font-mono text-[12.5px] leading-relaxed">
        {command ? (
          <div className="group flex items-start gap-2">
            <span className="select-none text-inverse-muted-foreground" aria-hidden>
              $
            </span>
            <code className={cn('min-w-0 flex-1 break-all', waiting && 'opacity-60')}>{command}</code>
            <CopyButton value={command} successText="SSH command copied" className={COPY_ON_INVERSE} />
          </div>
        ) : (
          <p className="flex items-center gap-2 text-inverse-muted-foreground">
            {instance.state === 'stopped' || instance.state === 'error' ? (
              '# no connection — start the instance to get a host port'
            ) : (
              <>
                <Loader2 className="size-3.5 animate-spin" aria-hidden />
                {`# ${instance.state === 'requested' || instance.state === 'placing' ? 'finding a GPU' : 'starting the container'}…`}
              </>
            )}
          </p>
        )}

        {hostKey?.fingerprint && (
          <div className="space-y-1 border-t border-inverse-border pt-3 text-[11.5px]">
            <p className="flex items-center gap-1.5 text-inverse-muted-foreground">
              <ShieldCheck className="size-3.5 text-success-light" aria-hidden />
              # host key reported by the agent — compare on first connect
            </p>
            <p className="break-all">
              <span className="text-inverse-muted-foreground">{(hostKey.type ?? '').replace(/^ssh-/, '').toUpperCase()} </span>
              {hostKey.fingerprint}
            </p>
          </div>
        )}

        {c.known_hosts.length > 0 && (
          <div className="flex items-start gap-2 text-[11px] text-inverse-muted-foreground">
            <span className="min-w-0 flex-1 truncate" title={c.known_hosts.join('\n')}>
              # known_hosts: {c.known_hosts[0]}
            </span>
            <CopyButton value={c.known_hosts.join('\n')} successText="known_hosts line copied" className={COPY_ON_INVERSE} />
          </div>
        )}
      </div>
    </div>
  );
}
