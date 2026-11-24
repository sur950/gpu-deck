'use client';

/**
 * Node detail → Files tab: the read-only agent file commands (agent >= 1.1.0).
 *
 *   Scan path   POST /nodes/{id}/scan → scan_path → model candidates on disk
 *   Hash files  POST /nodes/{id}/hash → hash_files → sha256 per file
 *
 * Both answer 200 when the agent finished within `wait_seconds`, else 202
 * with the command id; the panel then polls GET /nodes/{id}/commands/{cid}
 * every second until the state is terminal. The open command id lives in the
 * URL (`?tab=files&cmd=…`) so a result survives a refresh and can be shared.
 */

import { useMemo, useState } from 'react';
import Link from 'next/link';
import {
  BadgeCheck,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CircleX,
  Fingerprint,
  FolderSearch,
  History,
  Loader2,
  PackagePlus,
  TriangleAlert,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { EmptyState } from '@/components/shared/EmptyState';
import { StatusBadge } from '@/components/shared/StatusBadge';
import { CopyButton } from '@/components/shared/copy-button';
import { Segmented } from '@/components/automation/segmented';
import { COMMAND_BADGE } from '@/components/fleet/status';
import { useNow } from '@/components/fleet/expiry-countdown';
import { FormatBadge } from '@/components/models/model-badges';
import { useHashNodeFiles, useNodeCommand, useNodeCommands, useScanNodePath } from '@/hooks/api/fleet';
import { useUrlState } from '@/hooks/use-url-state';
import { cn } from '@/lib/utils';
import { formatBytes, formatRelativeTime } from '@/utils/format';
import {
  AGENT_COMMAND_STATE_LABELS,
  SCAN_KIND_LABELS,
  isTerminalCommand,
  versionAtLeast,
  type AgentCommand,
  type HashFilesResult,
  type HashedFile,
  type Node,
  type NodeFileCommandResult,
  type ScanCandidate,
  type ScanPathResult,
} from '@/types/fleet';

// First public agent release; every published agent implements scan_path / hash_files.
const MIN_AGENT = '1.0.0';
const FILE_COMMANDS = ['scan_path', 'hash_files'];
const URL_DEFAULTS = { cmd: '' };

/** Known model stores, expanded with the agent user's home on the node. */
const SCAN_ROOTS: Record<string, { path: string; label: string }[]> = {
  macos: [
    { path: '~/.ollama/models', label: 'Ollama' },
    { path: '~/.lmstudio/models', label: 'LM Studio' },
    { path: '~/.cache/huggingface/hub', label: 'HF cache' },
    { path: '~/models', label: '~/models' },
  ],
  linux: [
    { path: '~/.ollama/models', label: 'Ollama' },
    { path: '/usr/share/ollama/.ollama/models', label: 'Ollama (service)' },
    { path: '~/.cache/huggingface/hub', label: 'HF cache' },
    { path: '/models', label: '/models' },
  ],
};

/** Ollama blobs are named after their digest (`sha256-<hex>`): the expected hash is in the path. */
function digestFromPath(path: string): string | null {
  return /sha256[-:]([0-9a-f]{64})$/.exec(path)?.[1] ?? null;
}

function shortHash(hash: string): string {
  return `${hash.slice(0, 12)}…${hash.slice(-6)}`;
}

function basename(path: string): string {
  return path.replace(/\/+$/, '').split('/').pop() ?? path;
}

type Mode = 'scan' | 'hash';

interface LocalRun {
  /** What was asked, for the receipt line (the command endpoint does not echo args). */
  summary: string;
  startedAt: number;
  /** Expected sha256 per sent path (Ollama manifest digests from a scan). */
  expected: Record<string, string>;
}

// ─── Receipt (command id, state, progress, timing) ───────────────────────────

function CommandReceipt({ result, row, local }: { result: NodeFileCommandResult; row?: AgentCommand; local?: LocalRun }) {
  const running = !isTerminalCommand(result.state);
  const now = useNow(running ? 500 : 60_000);
  const startedAt = local?.startedAt ?? (row ? Date.parse(row.created_at) : null);
  const finishedSeconds = (result.result as { seconds?: number } | null)?.seconds;
  const elapsed = running && startedAt ? Math.max(0, (now - startedAt) / 1000) : null;
  const isHash = result.command === 'hash_files';
  const Icon = isHash ? Fingerprint : FolderSearch;
  const pct = Math.max(0, Math.min(100, result.progress_pct ?? 0));

  return (
    <div className="space-y-3 border-b border-border px-4 py-3.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <span className={cn('flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted/60', running && 'animate-pulse')}>
            <Icon className="size-4 text-muted-foreground" />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs font-semibold">{result.command}</span>
              <span className="font-mono text-[10.5px] text-muted-foreground" title={result.command_id}>
                {result.command_id.slice(0, 8)}
              </span>
              <CopyButton value={result.command_id} successText="Command ID copied" className="size-5" />
            </div>
            <div className="truncate font-mono text-[11px] text-muted-foreground" title={local?.summary}>
              {local?.summary ?? (row ? `sent ${formatRelativeTime(row.created_at)}` : 'agent command')}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-[11px] text-muted-foreground tabular-nums">
          {elapsed !== null && <span>{elapsed.toFixed(1)} s</span>}
          {!running && typeof finishedSeconds === 'number' && <span>took {finishedSeconds < 10 ? finishedSeconds.toFixed(2) : finishedSeconds.toFixed(0)} s</span>}
          <StatusBadge variant={COMMAND_BADGE[result.state]}>
            {running && result.progress_pct ? `${Math.round(result.progress_pct)}%` : AGENT_COMMAND_STATE_LABELS[result.state]}
          </StatusBadge>
        </div>
      </div>
      {running && (
        <div className="space-y-1">
          <div className="relative h-1 overflow-hidden rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={isHash ? pct : undefined} aria-label="Command progress">
            {isHash && pct > 0 ? (
              <div className="h-full rounded-full bg-brand transition-[width] duration-500" style={{ width: `${pct}%` }} />
            ) : (
              <div className="absolute inset-0 animate-[shimmer_1.4s_ease-in-out_infinite] bg-gradient-to-r from-transparent via-brand/70 to-transparent" />
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">
            {result.state === 'sent' || result.state === 'pending' ? 'Waiting for the agent to accept the command…' : isHash ? 'Hashing one file at a time — progress is by bytes.' : 'Walking the directory tree…'}
          </p>
        </div>
      )}
      {result.error_message && (
        <div className="flex items-start gap-2 rounded-md border border-error/30 bg-error/10 px-3 py-2 text-xs">
          <CircleX className="mt-px size-3.5 shrink-0 text-error" />
          <span>
            {result.error_code && <span className="font-mono font-semibold">{result.error_code}: </span>}
            {result.error_message}
          </span>
        </div>
      )}
    </div>
  );
}

// ─── Scan results ────────────────────────────────────────────────────────────

function CandidateRow({ candidate, nodeId, onHash, hashing }: { candidate: ScanCandidate; nodeId: string; onHash: (c: ScanCandidate) => void; hashing: boolean }) {
  const [open, setOpen] = useState(false);
  const s = candidate.suggested ?? {};
  const known = candidate.files.filter((f) => f.sha256).length;
  // Ollama candidates live inside a store (…/models/blobs/sha256-…): rescan the store, then pick the candidate.
  const storeMarker = ['/blobs/', '/manifests/'].find((m) => candidate.kind === 'ollama_blob' && candidate.path.includes(m));
  const scanRoot = storeMarker ? candidate.path.slice(0, candidate.path.indexOf(storeMarker)) : candidate.path;
  const registerHref = `/catalog/new?${new URLSearchParams({ source: 'local_path', path: scanRoot, pick: candidate.path, node: nodeId }).toString()}`;
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="group/cand">
      <div className={cn('flex items-start gap-2 px-4 py-3 transition-colors hover:bg-muted/30', open && 'bg-muted/30')}>
        <CollapsibleTrigger className="mt-0.5 flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={open ? 'Hide files' : 'Show files'}>
          <ChevronRight className={cn('size-3.5 transition-transform', open && 'rotate-90')} />
        </CollapsibleTrigger>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[13px] font-medium">{s.name ?? basename(candidate.path)}</span>
            <span className="rounded-sm border border-border px-1.5 text-[10.5px] text-muted-foreground">{SCAN_KIND_LABELS[candidate.kind] ?? candidate.kind}</span>
            <FormatBadge format={candidate.format} />
            {s.quantization && <span className="font-mono text-[11px] text-muted-foreground">{s.quantization}</span>}
            {s.architecture && <span className="font-mono text-[11px] text-muted-foreground">· {s.architecture}</span>}
            {s.context_size ? <span className="text-[11px] text-muted-foreground">· {Math.round(s.context_size / 1024)}k ctx</span> : null}
          </div>
          <div className="truncate font-mono text-[10.5px] text-muted-foreground" title={candidate.path}>
            {candidate.path}
          </div>
        </div>
        <div className="hidden shrink-0 text-right text-[11px] text-muted-foreground tabular-nums sm:block">
          <div className="font-medium text-foreground">{formatBytes(candidate.total_size_bytes)}</div>
          <div>
            {candidate.files.length} file{candidate.files.length === 1 ? '' : 's'}
            {known > 0 && ` · ${known} digest${known === 1 ? '' : 's'}`}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button variant="ghost" size="xs" className="text-muted-foreground hover:text-foreground" disabled={hashing} onClick={() => onHash(candidate)} title="sha256 of every file in this candidate">
            <Fingerprint className="size-3" />
            Hash
          </Button>
          <Link href={registerHref} className="inline-flex h-6 items-center gap-1 rounded-sm px-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground" title="Register this model in the catalog">
            <PackagePlus className="size-3" />
            Register
          </Link>
        </div>
      </div>
      <CollapsibleContent>
        <div className="border-t border-dashed border-border bg-muted/20 px-4 py-2 pl-11">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                <TableHead className="h-7 text-[11px]">File</TableHead>
                <TableHead className="h-7 text-[11px]">Format</TableHead>
                <TableHead className="h-7 text-right text-[11px]">Size</TableHead>
                <TableHead className="h-7 text-[11px]">SHA-256</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {candidate.files.map((f) => (
                <TableRow key={f.absolute_path} className="hover:bg-transparent">
                  <TableCell className="max-w-[340px] py-1.5">
                    <div className="truncate font-mono text-[11px]" title={f.absolute_path}>
                      {f.path}
                    </div>
                  </TableCell>
                  <TableCell className="py-1.5">
                    <FormatBadge format={f.format} />
                  </TableCell>
                  <TableCell className="py-1.5 text-right font-mono text-[11px] tabular-nums">{formatBytes(f.size_bytes)}</TableCell>
                  <TableCell className="py-1.5">
                    {f.sha256 ? (
                      <span className="inline-flex items-center gap-1 font-mono text-[11px] whitespace-nowrap" title={`${f.sha256} — from the Ollama manifest digest`}>
                        {shortHash(f.sha256)}
                        <BadgeCheck className="size-3 text-muted-foreground" aria-label="From the Ollama manifest" />
                      </span>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">not hashed</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          {candidate.metadata && Object.keys(candidate.metadata).length > 0 && (
            <details className="mt-2 text-[11px]">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">Raw metadata · {Object.keys(candidate.metadata).length} keys</summary>
              <pre className="mt-1 max-h-48 overflow-auto rounded-md bg-muted/60 p-2 font-mono text-[10.5px] leading-relaxed">{JSON.stringify(candidate.metadata, null, 2)}</pre>
            </details>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function ScanResultView({ result, nodeId, onHash, hashing }: { result: ScanPathResult; nodeId: string; onHash: (c: ScanCandidate) => void; hashing: boolean }) {
  const candidates = result.candidates ?? [];
  const total = candidates.reduce((s, c) => s + c.total_size_bytes, 0);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2.5 text-[11px] text-muted-foreground">
        <span className="font-mono text-foreground" title="Expanded on the node">
          {result.path}
        </span>
        <span className="tabular-nums">
          <span className="font-semibold text-foreground">{candidates.length}</span> candidate{candidates.length === 1 ? '' : 's'} · {formatBytes(total)}
        </span>
        {typeof result.visited === 'number' && <span className="tabular-nums">{result.visited.toLocaleString()} entries visited</span>}
      </div>
      {result.truncated && (
        <div className="flex items-center gap-2 border-b border-border bg-warning/10 px-4 py-2 text-xs">
          <TriangleAlert className="size-3.5 shrink-0 text-warning" />
          Stopped at the entry limit — narrow the path or raise the limit to see everything.
        </div>
      )}
      {candidates.length === 0 ? (
        <EmptyState icon={FolderSearch} title="No model files here" description="Nothing that looks like GGUF, safetensors, MLX, an Ollama store or an LM Studio folder within the search depth." />
      ) : (
        <div className="divide-y divide-border">
          {candidates.map((c) => (
            <CandidateRow key={c.path} candidate={c} nodeId={nodeId} onHash={onHash} hashing={hashing} />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Hash results ────────────────────────────────────────────────────────────

type Verdict = 'match' | 'mismatch' | 'error' | 'none';

function verdictOf(file: HashedFile, expected: Record<string, string>): Verdict {
  if (file.error || !file.sha256) return 'error';
  const want = expected[file.path] ?? digestFromPath(file.path);
  if (!want) return 'none';
  return want === file.sha256 ? 'match' : 'mismatch';
}

function HashResultView({ result, expected }: { result: HashFilesResult; expected: Record<string, string> }) {
  const files = result.files ?? [];
  const verdicts = files.map((f) => verdictOf(f, expected));
  const matched = verdicts.filter((v) => v === 'match').length;
  const mismatched = verdicts.filter((v) => v === 'mismatch').length;
  const failed = verdicts.filter((v) => v === 'error').length;
  const rate = result.seconds && result.seconds > 0 ? result.bytes_hashed / result.seconds : null;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2.5 text-[11px] text-muted-foreground tabular-nums">
        <span>
          <span className="font-semibold text-foreground">{files.length - failed}</span> of {files.length} hashed · {formatBytes(result.bytes_hashed)}
          {rate ? ` · ${formatBytes(rate)}/s` : ''}
        </span>
        {result.root && <span className="font-mono">root {result.root}</span>}
        {matched > 0 && (
          <span className="inline-flex items-center gap-1 text-success-dark dark:text-success-light">
            <CircleCheck className="size-3" /> {matched} match{matched === 1 ? 'es' : ''} the expected digest
          </span>
        )}
        {mismatched > 0 && (
          <span className="inline-flex items-center gap-1 text-error-dark dark:text-error-light">
            <CircleX className="size-3" /> {mismatched} mismatch{mismatched === 1 ? '' : 'es'}
          </span>
        )}
      </div>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="pl-4">File</TableHead>
              <TableHead className="text-right">Size</TableHead>
              <TableHead>SHA-256</TableHead>
              <TableHead className="pr-4">Check</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {files.map((f, i) => {
              const verdict = verdicts[i];
              return (
                <TableRow key={f.path}>
                  <TableCell className="max-w-[320px] pl-4">
                    <div className="truncate font-mono text-[11px]" title={f.path}>
                      {basename(f.path)}
                    </div>
                    <div className="truncate font-mono text-[10px] text-muted-foreground" title={f.path}>
                      {f.path}
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-[11px] tabular-nums">{f.size_bytes !== null && f.size_bytes !== undefined ? formatBytes(f.size_bytes) : '—'}</TableCell>
                  <TableCell>
                    {f.sha256 ? (
                      <span className="inline-flex items-center gap-0.5">
                        <span className="font-mono text-[11px]" title={f.sha256}>
                          {shortHash(f.sha256)}
                        </span>
                        <CopyButton value={f.sha256} successText="SHA-256 copied" className="size-5" />
                      </span>
                    ) : (
                      <span className="text-[11px] text-error-dark dark:text-error-light">
                        {f.error ? `${f.error.code}: ${f.error.message}` : 'no hash'}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="pr-4">
                    {verdict === 'match' ? (
                      <StatusBadge variant="success">
                        <CircleCheck className="mr-1 size-3" />
                        Matches
                      </StatusBadge>
                    ) : verdict === 'mismatch' ? (
                      <StatusBadge variant="error" className="whitespace-nowrap">
                        <CircleX className="mr-1 size-3" />
                        Mismatch
                      </StatusBadge>
                    ) : verdict === 'error' ? (
                      <StatusBadge variant="failed">Unreadable</StatusBadge>
                    ) : (
                      <span className="text-[11px] text-muted-foreground">no reference</span>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

// ─── Forms ───────────────────────────────────────────────────────────────────

function ScanForm({ node, disabled, pending, onRun }: { node: Node; disabled: boolean; pending: boolean; onRun: (body: { path: string; max_depth: number; read_metadata: boolean; recursive: boolean }) => void }) {
  const roots = SCAN_ROOTS[node.os ?? 'linux'] ?? SCAN_ROOTS.linux;
  const [path, setPath] = useState(roots[0].path);
  const [depth, setDepth] = useState('4');
  const [metadata, setMetadata] = useState(true);
  const [recursive, setRecursive] = useState(true);
  const run = (p = path) => {
    setPath(p);
    onRun({ path: p.trim(), max_depth: Number(depth), read_metadata: metadata, recursive });
  };
  const valid = path.trim().startsWith('/') || path.trim().startsWith('~');
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) run();
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="scan-path" className="text-xs">
          Path on {node.name}
        </Label>
        <div className="flex gap-2">
          <Input id="scan-path" value={path} onChange={(e) => setPath(e.target.value)} placeholder="~/.ollama/models or /models/model.gguf" className="h-9 font-mono text-xs" aria-invalid={!!path.trim() && !valid} />
          <Button type="submit" size="sm" className="h-9 rounded-sm" disabled={disabled || pending || !valid}>
            {pending ? <Loader2 className="size-4 animate-spin" /> : <FolderSearch className="size-4" />}
            Scan
          </Button>
        </div>
        <p className="text-[11px] text-muted-foreground">Absolute, or <span className="font-mono">~/…</span> (expanded to the agent user&apos;s home on the node). A file or a directory.</p>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        {roots.map((r) => (
          <button
            key={r.path}
            type="button"
            disabled={disabled || pending}
            onClick={() => run(r.path)}
            className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[10.5px] transition-colors hover:border-foreground/30 hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            title={`Scan ${r.path}`}
          >
            {r.label}
          </button>
        ))}
      </div>
      <div className="grid gap-3 rounded-md border border-border bg-muted/30 p-3 sm:grid-cols-3">
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground">Depth</Label>
          <Select value={depth} onValueChange={(v) => setDepth(String(v))}>
            <SelectTrigger size="sm" className="w-full" aria-label="Search depth">
              <SelectValue>{(v: unknown) => `${String(v)} level${v === '1' ? '' : 's'}`}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {[1, 2, 3, 4, 6, 8, 10].map((d) => (
                <SelectItem key={d} value={String(d)}>
                  {d} level{d === 1 ? '' : 's'}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex cursor-pointer items-center justify-between gap-2 sm:flex-col sm:items-start">
          <span className="text-[11px] text-muted-foreground">Recursive</span>
          <Switch checked={recursive} onCheckedChange={setRecursive} size="sm" />
        </label>
        <label className="flex cursor-pointer items-center justify-between gap-2 sm:flex-col sm:items-start">
          <span className="text-[11px] text-muted-foreground">Read GGUF / config headers</span>
          <Switch checked={metadata} onCheckedChange={setMetadata} size="sm" />
        </label>
      </div>
    </form>
  );
}

function HashForm({ disabled, pending, paths, setPaths, onRun }: { disabled: boolean; pending: boolean; paths: string; setPaths: (v: string) => void; onRun: (body: { paths: string[]; root: string | null }) => void }) {
  const [root, setRoot] = useState('');
  const list = paths
    .split('\n')
    .map((p) => p.trim())
    .filter(Boolean);
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (list.length) onRun({ paths: list, root: root.trim() || null });
      }}
    >
      <div className="space-y-1.5">
        <Label htmlFor="hash-paths" className="text-xs">
          Files · one per line
        </Label>
        <Textarea
          id="hash-paths"
          value={paths}
          onChange={(e) => setPaths(e.target.value)}
          rows={5}
          placeholder={'~/.ollama/models/blobs/sha256-7f40…\n/models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf'}
          className="max-h-56 font-mono text-[11px] leading-relaxed"
        />
        <p className="text-[11px] text-muted-foreground">
          {list.length ? `${list.length} file${list.length === 1 ? '' : 's'} · ` : ''}hashed one at a time on the node; an unreadable file gets its own error and does not stop the rest.
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="hash-root" className="text-xs">
          Root <span className="font-normal text-muted-foreground">(optional)</span>
        </Label>
        <Input id="hash-root" value={root} onChange={(e) => setRoot(e.target.value)} placeholder="/models — paths are then relative and must stay inside it" className="h-9 font-mono text-xs" />
      </div>
      <Button type="submit" size="sm" className="rounded-sm" disabled={disabled || pending || list.length === 0}>
        {pending ? <Loader2 className="size-4 animate-spin" /> : <Fingerprint className="size-4" />}
        Hash {list.length > 1 ? `${list.length} files` : 'file'}
      </Button>
    </form>
  );
}

// ─── Panel ───────────────────────────────────────────────────────────────────

function RecentCommands({ rows, activeId, onOpen }: { rows: AgentCommand[]; activeId: string; onOpen: (id: string) => void }) {
  if (rows.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        <History className="size-3" />
        Recent
      </div>
      <ul className="divide-y divide-border rounded-md border border-border">
        {rows.slice(0, 6).map((cmd) => (
          <li key={cmd.id}>
            <button
              type="button"
              onClick={() => onOpen(cmd.id)}
              className={cn('flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40', activeId === cmd.id && 'bg-muted/60 shadow-[inset_3px_0_0_rgb(var(--brand))]')}
            >
              <span className="flex min-w-0 items-center gap-2">
                {cmd.command_type === 'hash_files' ? <Fingerprint className="size-3.5 shrink-0 text-muted-foreground" /> : <FolderSearch className="size-3.5 shrink-0 text-muted-foreground" />}
                <span className="font-mono text-[11px]">{cmd.command_type}</span>
                <span className="truncate text-[11px] text-muted-foreground">{formatRelativeTime(cmd.created_at)}</span>
              </span>
              <StatusBadge variant={COMMAND_BADGE[cmd.state]} className="shrink-0">
                {AGENT_COMMAND_STATE_LABELS[cmd.state]}
              </StatusBadge>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function NodeFilesPanel({ node }: { node: Node }) {
  const { params, setParams } = useUrlState(URL_DEFAULTS);
  const [mode, setMode] = useState<Mode>('scan');
  const [hashPaths, setHashPaths] = useState('');
  const [runs, setRuns] = useState<Record<string, LocalRun>>({});
  const scan = useScanNodePath();
  const hash = useHashNodeFiles();
  const commands = useNodeCommands(node.id);
  const command = useNodeCommand(node.id, params.cmd || null);

  const recent = useMemo(() => (commands.data?.items ?? []).filter((c) => FILE_COMMANDS.includes(c.command_type)), [commands.data]);
  const supported = versionAtLeast(node.agent_version, MIN_AGENT);
  const connected = node.status === 'online' || node.status === 'draining' || node.status === 'drained' || node.status === 'unhealthy';
  const disabled = !supported || !connected;
  const result = command.data;
  const running = !!result && !isTerminalCommand(result.state);

  function remember(res: NodeFileCommandResult, run: Omit<LocalRun, 'startedAt'>, startedAt: number) {
    setRuns((prev) => ({ ...prev, [res.command_id]: { ...run, startedAt } }));
    setParams({ cmd: res.command_id });
  }

  function runScan(body: { path: string; max_depth: number; read_metadata: boolean; recursive: boolean }) {
    const startedAt = Date.now();
    scan.mutate(
      { nodeId: node.id, body: { ...body, wait_seconds: 5 } },
      { onSuccess: (res) => remember(res, { summary: `scan ${body.path} · depth ${body.max_depth}`, expected: {} }, startedAt) },
    );
  }

  function runHash(body: { paths: string[]; root: string | null }, expected: Record<string, string> = {}) {
    const startedAt = Date.now();
    hash.mutate(
      { nodeId: node.id, body: { ...body, wait_seconds: 1 } },
      {
        onSuccess: (res) =>
          remember(res, { summary: `hash ${body.paths.length === 1 ? body.paths[0] : `${body.paths.length} files`}${body.root ? ` in ${body.root}` : ''}`, expected }, startedAt),
      },
    );
  }

  function hashCandidate(candidate: ScanCandidate) {
    const paths = candidate.files.map((f) => f.absolute_path);
    const expected = Object.fromEntries(candidate.files.filter((f) => f.sha256).map((f) => [f.absolute_path, f.sha256 as string]));
    setHashPaths(paths.join('\n'));
    setMode('hash');
    runHash({ paths, root: null }, expected);
  }

  const scanResult = result?.command === 'scan_path' && result.state === 'succeeded' ? (result.result as ScanPathResult | null) : null;
  const hashResult = result?.command === 'hash_files' && result.state === 'succeeded' ? (result.result as HashFilesResult | null) : null;
  const local = params.cmd ? runs[params.cmd] : undefined;

  return (
    <div className="grid items-start gap-6 xl:grid-cols-[400px_minmax(0,1fr)]">
      <Card className="rounded-md shadow-premium">
        <CardHeader className="space-y-3">
          <div>
            <CardTitle>Files on disk</CardTitle>
            <CardDescription>Read-only agent commands: find model weights under a path, or compute SHA-256 of files. Nothing is moved or written.</CardDescription>
          </div>
          <Segmented<Mode>
            value={mode}
            onChange={setMode}
            aria-label="File command"
            options={[
              { value: 'scan', label: 'Scan path', icon: FolderSearch },
              { value: 'hash', label: 'Hash files', icon: Fingerprint },
            ]}
          />
        </CardHeader>
        <CardContent className="space-y-5">
          {!supported ? (
            <div className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-xs">
              <CircleAlert className="mt-px size-3.5 shrink-0 text-warning" />
              <span>
                Needs agent {MIN_AGENT} or newer — this node runs <span className="font-mono">{node.agent_version ?? 'an unknown version'}</span>. Upgrade the agent from the{' '}
                <Link href="/nodes/join" className="underline underline-offset-2">
                  join page
                </Link>
                .
              </span>
            </div>
          ) : !connected ? (
            <div className="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-xs text-muted-foreground">
              <CircleAlert className="mt-px size-3.5 shrink-0" />
              The agent is not connected — commands are delivered over its hub connection.
            </div>
          ) : null}
          {mode === 'scan' ? (
            <ScanForm node={node} disabled={disabled} pending={scan.isPending} onRun={runScan} />
          ) : (
            <HashForm disabled={disabled} pending={hash.isPending} paths={hashPaths} setPaths={setHashPaths} onRun={(body) => runHash(body)} />
          )}
          <RecentCommands rows={recent} activeId={params.cmd} onOpen={(id) => setParams({ cmd: id })} />
        </CardContent>
      </Card>

      <Card className="gap-0 overflow-hidden rounded-md py-0 shadow-premium">
        {!params.cmd ? (
          <CardContent className="py-6">
            <EmptyState
              icon={mode === 'scan' ? FolderSearch : Fingerprint}
              title={mode === 'scan' ? 'Scan a folder on this node' : 'Hash files on this node'}
              description={
                mode === 'scan'
                  ? 'Finds GGUF, safetensors and MLX weights, Ollama stores (with their manifest digests) and LM Studio or Hugging Face caches. Register a result straight into the catalog.'
                  : 'SHA-256 per file, streamed with progress. Ollama blobs are checked against the digest in their name.'
              }
            />
          </CardContent>
        ) : command.isLoading && !result ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="h-10 animate-pulse rounded bg-muted" />
            ))}
          </div>
        ) : command.isError || !result ? (
          <CardContent className="py-6">
            <EmptyState
              icon={CircleAlert}
              title="Command not found"
              description="It may belong to another node, or the control plane is unreachable."
              action={
                <Button variant="outline" size="sm" className="rounded-sm" onClick={() => setParams({ cmd: '' })}>
                  Clear
                </Button>
              }
            />
          </CardContent>
        ) : (
          <>
            <CommandReceipt result={result} row={recent.find((r) => r.id === result.command_id)} local={local} />
            {scanResult ? (
              <ScanResultView result={scanResult} nodeId={node.id} onHash={hashCandidate} hashing={hash.isPending || (running && result.command === 'hash_files')} />
            ) : hashResult ? (
              <HashResultView result={hashResult} expected={local?.expected ?? {}} />
            ) : running ? (
              <div className="space-y-2 p-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="h-12 animate-pulse rounded bg-muted/70" style={{ animationDelay: `${i * 120}ms` }} />
                ))}
              </div>
            ) : (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">No result — the agent ended the command as {AGENT_COMMAND_STATE_LABELS[result.state].toLowerCase()}.</p>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
