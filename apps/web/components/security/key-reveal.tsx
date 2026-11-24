'use client';

import { useState } from 'react';
import { KeyRound, TriangleAlert } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { CopyButton } from '@/components/shared/copy-button';
import { useSystemInfo } from '@/hooks/api/models';
import { getGatewayUrl } from '@/utils/gateway-url';

/** Fallback serving base before /system/info answers: the admin API host with port 8181 and no /api/v1. */
function servingBase(): string {
  try {
    const u = new URL(getGatewayUrl(), typeof window === 'undefined' ? 'http://localhost' : window.location.origin);
    if (u.port === '8180') u.port = '8181';
    return `${u.protocol}//${u.host}`;
  } catch {
    return 'https://gpu-deck.example.com';
  }
}

interface KeyRevealProps {
  secret: string;
  name: string;
  /** Called with true once the admin confirms the key is stored. */
  onConfirmedChange: (confirmed: boolean) => void;
}

/**
 * The plaintext key, shown exactly once: big mono field with copy, a
 * ready-to-run curl for each API format, and a "stored it" checkbox that
 * gates closing.
 */
export function KeyReveal({ secret, name, onConfirmedChange }: KeyRevealProps) {
  const [confirmed, setConfirmed] = useState(false);
  const info = useSystemInfo();
  // /system/info knows the public serving URL (PUBLIC_SERVING_URL); the guess covers the first render.
  const base = (info.data?.api_layer_url ?? servingBase()).replace(/\/+$/, '');
  const prefix = info.data?.serving?.openai_prefix ?? '/v1';
  const curl = `curl ${base}${prefix}/chat/completions \\\n  -H "Authorization: Bearer ${secret}" \\\n  -H "Content-Type: application/json" \\\n  -d '{"model": "qwen3-0.6b", "messages": [{"role": "user", "content": "Hello"}]}'`;
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-xs text-warning-dark dark:text-warning-light">
        <TriangleAlert className="mt-px size-3.5 shrink-0" />
        <span>
          Copy <b>{name}</b> now. Only a SHA-256 of it is stored, so it cannot be shown again — if it is lost, rotate the key.
        </span>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/40 p-3">
        <KeyRound className="size-4 shrink-0 text-muted-foreground" />
        <code className="min-w-0 flex-1 break-all font-mono text-[13px] font-medium">{secret}</code>
        <CopyButton value={secret} successText="API key copied" label="Copy" size="sm" variant="outline" />
      </div>
      <div className="space-y-1.5">
        <p className="flex items-center justify-between text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          Try it (OpenAI format)
          <CopyButton value={curl} successText="curl copied" />
        </p>
        <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-[11px] leading-relaxed">{curl}</pre>
        <p className="text-[11px] text-muted-foreground">The same key works for Anthropic (`x-api-key` on /v1/messages) and Ollama (/api/chat) clients.</p>
      </div>
      <label className="flex items-center gap-2 text-xs font-medium">
        <Checkbox
          checked={confirmed}
          onCheckedChange={(v) => {
            setConfirmed(!!v);
            onConfirmedChange(!!v);
          }}
        />
        I have stored this key somewhere safe
      </label>
    </div>
  );
}
