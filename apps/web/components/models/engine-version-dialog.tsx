'use client';

import { useState } from 'react';
import { Boxes } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { FormDialog, Field } from '@/components/shared/form-dialog';
import { KeyValueEditor, recordToRows, rowsToFlags, type KeyValueRow } from '@/components/shared/key-value-editor';
import { useCreateEngineVersion, useUpdateEngineVersion } from '@/hooks/api/models';
import type { Engine, EngineVersion, EngineVersionStatus } from '@/types/models';

interface Artifact {
  url?: string;
  sha256?: string;
  kind?: string;
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

/**
 * Add or edit an engine version: a container image, or per-platform native
 * binaries, plus the launch template, health check and metrics endpoint the
 * node agent uses verbatim. This is also how a custom engine is registered.
 */
export function EngineVersionDialog({ engine, version, open, onOpenChange }: { engine: Engine; version?: EngineVersion | null; open: boolean; onOpenChange: (open: boolean) => void }) {
  const create = useCreateEngineVersion();
  const update = useUpdateEngineVersion();
  const container = engine.runtime === 'container';
  const artifacts = (version?.artifacts ?? {}) as Record<string, Artifact>;
  const template = (version?.launch_template ?? {}) as { command?: string[] };
  const health = (version?.health_config ?? {}) as { path?: string; interval_seconds?: number };
  const metrics = (version?.metrics_config ?? {}) as { path?: string; format?: string };

  const [label, setLabel] = useState(version?.version ?? '');
  const [image, setImage] = useState(version?.image ?? '');
  const [linuxUrl, setLinuxUrl] = useState(str(artifacts['linux/x86_64']?.url));
  const [linuxSha, setLinuxSha] = useState(str(artifacts['linux/x86_64']?.sha256));
  const [macUrl, setMacUrl] = useState(str(artifacts['macos/aarch64']?.url));
  const [macSha, setMacSha] = useState(str(artifacts['macos/aarch64']?.sha256));
  const [command, setCommand] = useState((template.command ?? (container ? ['--host', '0.0.0.0', '--port', '{{port}}', '--model', '{{model_path}}'] : [])).join(' '));
  const [healthPath, setHealthPath] = useState(health.path ?? '/health');
  const [metricsPath, setMetricsPath] = useState(metrics.path ?? '/metrics');
  const [flags, setFlags] = useState<KeyValueRow[]>(() => recordToRows(version?.default_launch_flags));
  const [minDriver, setMinDriver] = useState(version?.min_driver_version ?? '');
  const [status, setStatus] = useState<EngineVersionStatus>(version?.status ?? 'available');
  const [isDefault, setIsDefault] = useState(version?.is_default ?? (engine.versions?.length ?? 0) === 0);

  const valid = !!label.trim() && (!container || !!image.trim()) && command.includes('{{port}}');
  const pending = create.isPending || update.isPending;

  function submit() {
    const body = {
      version: label.trim(),
      image: container ? image.trim() : null,
      artifacts: container
        ? {}
        : Object.fromEntries(
            [
              ['linux/x86_64', linuxUrl, linuxSha],
              ['macos/aarch64', macUrl, macSha],
            ]
              .filter(([, url]) => url)
              .map(([k, url, sha]) => [k, { url, sha256: sha || null, kind: 'tarball' }]),
          ),
      launch_template: { command: command.trim().split(/\s+/), env: {} },
      health_config: { path: healthPath || '/health', interval_seconds: health.interval_seconds ?? 5, timeout_seconds: 3, healthy_statuses: [200] },
      metrics_config: metricsPath ? { path: metricsPath, format: 'prometheus', scrape_interval_seconds: 2 } : { derive_from_proxy: true },
      default_launch_flags: rowsToFlags(flags),
      min_driver_version: minDriver.trim() || null,
      status,
      is_default: isDefault,
    };
    if (version) update.mutate({ engineId: engine.id, versionId: version.id, body }, { onSuccess: () => onOpenChange(false) });
    else create.mutate({ engineId: engine.id, body }, { onSuccess: () => onOpenChange(false) });
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      icon={<Boxes className="size-4 text-muted-foreground" />}
      title={version ? `Edit ${engine.display_name} ${version.version}` : `Add a ${engine.display_name} version`}
      description={
        container
          ? 'Container engines run through the node agent with the image below. The command is passed as arguments; {{port}}, {{model_path}}, {{context_size}} are filled per instance.'
          : 'Native binaries are downloaded per platform and checked against the SHA-256. {{port}}, {{model_path}}, {{context_size}} are filled per instance.'
      }
      submitLabel={version ? 'Save version' : 'Add version'}
      widthClass="sm:max-w-2xl"
      isPending={pending}
      submitDisabled={!valid}
      footerNote={!command.includes('{{port}}') ? 'The command must bind {{port}} (localhost only).' : undefined}
      onSubmit={submit}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Version" htmlFor="ev-version">
          <Input id="ev-version" value={label} onChange={(e) => setLabel(e.target.value)} placeholder={container ? 'v1.2.0' : 'b8798'} className="font-mono text-sm" />
        </Field>
        <Field label="Status">
          <Select value={status} onValueChange={(v) => setStatus(v as EngineVersionStatus)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="available">available</SelectItem>
              <SelectItem value="deprecated">deprecated</SelectItem>
              <SelectItem value="disabled">disabled</SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <Field label="Min driver" htmlFor="ev-driver">
          <Input id="ev-driver" value={minDriver} onChange={(e) => setMinDriver(e.target.value)} placeholder="550.54" className="font-mono text-sm" />
        </Field>
      </div>
      {container ? (
        <Field label="Image" hint="Pinned tag or digest; pulled by the agent with its own credentials." htmlFor="ev-image">
          <Input id="ev-image" value={image} onChange={(e) => setImage(e.target.value)} placeholder="registry.example.com/llm/server:1.2.0@sha256:…" className="font-mono text-sm" />
        </Field>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="linux/x86_64 artifact" htmlFor="ev-linux">
            <Input id="ev-linux" value={linuxUrl} onChange={(e) => setLinuxUrl(e.target.value)} placeholder="https://…/linux-x86_64.tar.gz" className="font-mono text-xs" />
            <Input value={linuxSha} onChange={(e) => setLinuxSha(e.target.value)} placeholder="sha256" className="mt-1.5 font-mono text-xs" aria-label="linux sha256" />
          </Field>
          <Field label="macos/aarch64 artifact" htmlFor="ev-mac">
            <Input id="ev-mac" value={macUrl} onChange={(e) => setMacUrl(e.target.value)} placeholder="https://…/macos-arm64.tar.gz" className="font-mono text-xs" />
            <Input value={macSha} onChange={(e) => setMacSha(e.target.value)} placeholder="sha256" className="mt-1.5 font-mono text-xs" aria-label="macos sha256" />
          </Field>
        </div>
      )}
      <Field label="Launch command" hint="Space separated. Engines always listen on 127.0.0.1 — the agent's mTLS proxy is the only way in." htmlFor="ev-cmd">
        <Textarea id="ev-cmd" rows={2} value={command} onChange={(e) => setCommand(e.target.value)} className="font-mono text-xs" />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Health path" htmlFor="ev-health">
          <Input id="ev-health" value={healthPath} onChange={(e) => setHealthPath(e.target.value)} className="font-mono text-sm" />
        </Field>
        <Field label="Metrics path" hint="Prometheus text; empty = derive from proxy counters." htmlFor="ev-metrics">
          <Input id="ev-metrics" value={metricsPath} onChange={(e) => setMetricsPath(e.target.value)} className="font-mono text-sm" />
        </Field>
      </div>
      <Field label="Default launch flags">
        <KeyValueEditor rows={flags} onChange={setFlags} keyPlaceholder="--max-num-seqs" valuePlaceholder="64" addLabel="Add flag" />
      </Field>
      <label className="flex items-center justify-between gap-4 rounded-lg border border-border bg-muted/30 p-3 text-xs">
        <span>
          <span className="block font-medium">Default version</span>
          <span className="text-muted-foreground">Used by auto-pick and by deployments that do not pin a version.</span>
        </span>
        <Switch checked={isDefault} onCheckedChange={setIsDefault} aria-label="Default version" />
      </label>
    </FormDialog>
  );
}
