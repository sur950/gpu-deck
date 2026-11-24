'use client';

import { useState } from 'react';
import { Apple, Container, Terminal } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CodeBlock } from '@/components/shared/code-block';
import { INSTALL_PLATFORM_LABELS, type InstallPlatform } from '@/types/fleet';

export interface InstallContext {
  hubUrl: string;
  token: string;
  caFingerprint: string | null;
  nodeName: string | null;
  labels: Record<string, string>;
  /** URL prefix that serves `install.sh` and `downloads/` (the hub's `<hub>/agent/v1`); the join token authorises it. */
  downloadBase: string;
  /** sha256 of `<hub>/agent/v1/ca.pem`, pinned before anything is fetched from the hub (null = system trust store). */
  caPemSha256: string | null;
  /** Agent build version to fetch (`latest` when unknown). */
  version: string | null;
  /** The token enrols several machines (`max_uses` > 1). */
  multiUse?: boolean;
}

/** Download + sha256-verify the agent build for `target` from the hub with the join token. */
export function downloadSteps(ctx: InstallContext, target: string, sha256Tool: string): string[] {
  const base = `${ctx.downloadBase}/downloads/${ctx.version ?? 'latest'}/${target}`;
  const auth = `-H 'X-GPUDeck-Join-Token: ${ctx.token}'`;
  const pinned = ctx.caPemSha256 && ctx.downloadBase.endsWith('/agent/v1');
  const ca = pinned ? ' --cacert gpudeck-ca.pem' : '';
  return [
    ...(pinned
      ? [
          `curl -fsSk -o gpudeck-ca.pem ${ctx.downloadBase}/ca.pem   # the hub's internal CA, checked against the pinned sha256:`,
          `echo "${ctx.caPemSha256}  gpudeck-ca.pem" | ${sha256Tool} -c -`,
        ]
      : []),
    `curl -fsSL --retry 3 --retry-delay 2${ca} ${auth} -o gpudeck-agent.download ${base}`,
    `echo "$(curl -fsSL${ca} ${auth} ${base}/sha256 | cut -d' ' -f1)  gpudeck-agent.download" | ${sha256Tool} -c -`,
    'if gzip -t gpudeck-agent.download 2>/dev/null; then tar -xzf gpudeck-agent.download; else mv gpudeck-agent.download gpudeck-agent; fi',
  ];
}

const BIN = '/usr/local/bin/gpudeck-agent';

function joinFlags(ctx: InstallContext, indent = '  '): string {
  const parts = [`--hub ${ctx.hubUrl}`, `--token ${ctx.token}`];
  if (ctx.nodeName) parts.push(`--name ${ctx.nodeName}`);
  if (ctx.caFingerprint) parts.push(`--ca-fingerprint ${ctx.caFingerprint}`);
  return parts.map((p) => `${indent}${p}`).join(' \\\n');
}

export function linuxSteps(ctx: InstallContext): { title: string; code: string }[] {
  return [
    {
      title: '1 · Download the agent from the hub, verify it and install it',
      code: [
        'case "$(uname -m)" in x86_64|amd64) T=x86_64-unknown-linux-gnu ;; aarch64|arm64) T=aarch64-unknown-linux-gnu ;; esac',
        ...downloadSteps(ctx, '$T', 'sha256sum'),
        `sudo install -m 0755 gpudeck-agent ${BIN}`,
        'sudo useradd --system --home-dir /var/lib/gpudeck --shell /usr/sbin/nologin gpudeck 2>/dev/null || true',
        'sudo usermod -aG video,render gpudeck',
        'sudo install -d -o gpudeck -g gpudeck -m 0750 /var/lib/gpudeck',
      ].join('\n'),
    },
    {
      title: `2 · Join with the ${ctx.multiUse ? 'enrolment' : 'one-time'} token`,
      code: `sudo -u gpudeck ${BIN} join \\\n${joinFlags(ctx)}`,
    },
    {
      title: '3 · Run it as a systemd service',
      code: [
        "sudo tee /etc/systemd/system/gpudeck-agent.service >/dev/null <<'UNIT'",
        '[Unit]',
        'Description=GPU Deck node agent',
        'After=network-online.target',
        'Wants=network-online.target',
        '',
        '[Service]',
        'User=gpudeck',
        'Group=gpudeck',
        'SupplementaryGroups=video render',
        `ExecStart=${BIN} run`,
        'Restart=always',
        'RestartSec=5',
        '# engines outlive agent restarts and upgrades; the next start re-adopts them',
        'KillMode=process',
        'LimitNOFILE=65536',
        'NoNewPrivileges=true',
        'ProtectSystem=strict',
        'ReadWritePaths=/var/lib/gpudeck',
        'StateDirectory=gpudeck gpudeck/tmp',
        'StateDirectoryMode=0700',
        '# writable /tmp for engines that survives agent restarts (PrivateTmp is wiped on restart)',
        'BindPaths=/var/lib/gpudeck/tmp:/tmp',
        '',
        '[Install]',
        'WantedBy=multi-user.target',
        'UNIT',
        'sudo systemctl daemon-reload',
        'sudo systemctl enable --now gpudeck-agent',
        'journalctl -u gpudeck-agent -f',
      ].join('\n'),
    },
  ];
}

export function macosSteps(ctx: InstallContext): { title: string; code: string }[] {
  const label = 'io.github.sur950.gpudeck-agent';
  return [
    {
      title: '1 · Download the Apple Silicon build, verify it and install it',
      code: [
        ...downloadSteps(ctx, 'aarch64-apple-darwin', 'shasum -a 256'),
        `sudo install -m 0755 gpudeck-agent ${BIN}`,
        'sudo install -d -o "$USER" -m 0750 /usr/local/var/gpudeck /usr/local/var/log',
      ].join('\n'),
    },
    {
      title: `2 · Join with the ${ctx.multiUse ? 'enrolment' : 'one-time'} token`,
      code: `${BIN} join \\\n${joinFlags(ctx)}`,
    },
    {
      title:
        '3 · Run it as a LaunchAgent (your login session, so Metal and MLX get the GPU)',
      code: [
        `tee ~/Library/LaunchAgents/${label}.plist >/dev/null <<'PLIST'`,
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
        '<plist version="1.0">',
        '<dict>',
        `  <key>Label</key><string>${label}</string>`,
        '  <key>ProgramArguments</key>',
        `  <array><string>${BIN}</string><string>run</string></array>`,
        '  <key>RunAtLoad</key><true/>',
        '  <key>KeepAlive</key><true/>',
        '  <key>AbandonProcessGroup</key><true/>',
        '  <key>ProcessType</key><string>Interactive</string>',
        '  <key>StandardErrorPath</key><string>/usr/local/var/log/gpudeck-agent.log</string>',
        '</dict>',
        '</plist>',
        'PLIST',
        `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/${label}.plist`,
        `launchctl print gui/$(id -u)/${label} | head -20`,
      ].join('\n'),
    },
  ];
}

export function kubernetesSteps(
  ctx: InstallContext,
): { title: string; code: string }[] {
  if (ctx.multiUse) {
    const labels = Object.entries(ctx.labels)
      .map(([k, v]) => `${k}=${v}`)
      .join(',');
    return [
      {
        title:
          '1 · Store the enrolment token in a Secret (every GPU node of the pool uses it once)',
        code: [
          'kubectl create namespace gpu-deck --dry-run=client -o yaml | kubectl apply -f -',
          'kubectl -n gpu-deck create secret generic gpudeck-agent-join \\',
          `  --from-literal=GPUDECK_AGENT_JOIN_TOKEN=${ctx.token}`,
          'kubectl label node <gpu-node> gpudeck.sur950.github.io/gpu-node=true   # each GPU node of the pool',
        ].join('\n'),
      },
      {
        title: '2 · Run the agent DaemonSet from the GPU Deck Helm chart',
        code: [
          'helm upgrade --install gpu-deck oci://ghcr.io/sur950/charts/gpu-deck -n gpu-deck --reuse-values \\',
          '  --set nodeAgent.enabled=true --set nodeAgent.joinTokenSecret=gpudeck-agent-join \\',
          `  --set nodeAgent.hubUrl=${ctx.hubUrl}${ctx.caFingerprint ? ` \\\n  --set nodeAgent.caFingerprint=${ctx.caFingerprint}` : ''}${labels ? ` \\\n  --set-string 'nodeAgent.labels.${labels.replace(/,/g, "' --set-string 'nodeAgent.labels.")}'` : ''}`,
          "# container engines (vLLM, SGLang, TensorRT-LLM) through the node's Docker socket:",
          '#   --set nodeAgent.containerRuntime.enabled=true --set nodeAgent.containerRuntime.socketGroupId=<docker gid>',
          'kubectl -n gpu-deck rollout status ds/gpu-deck-node-agent',
        ].join('\n'),
      },
    ];
  }
  const node = ctx.nodeName || '<k8s-node-name>';
  const secret = `gpudeck-join-${node.replace(/[^a-z0-9-]/gi, '-').toLowerCase()}`;
  const labelLines = Object.entries(ctx.labels).map(
    ([k, v]) =>
      `            - name: GPUDECK_AGENT_LABELS_${k.toUpperCase().replace(/[^A-Z0-9]/g, '_')}\n              value: "${v}"`,
  );
  return [
    {
      title:
        '1 · Store the token in a Secret (a one-time token joins one node; issue a multi-use token for a pool)',
      code: [
        'kubectl create namespace gpudeck-system --dry-run=client -o yaml | kubectl apply -f -',
        `kubectl -n gpudeck-system create secret generic ${secret} \\`,
        `  --from-literal=token=${ctx.token}${ctx.caFingerprint ? ` \\\n  --from-literal=ca-fingerprint=${ctx.caFingerprint}` : ''}`,
      ].join('\n'),
    },
    {
      title: `2 · Run the agent on ${ctx.nodeName ? `the GPU node "${node}"` : 'the GPU node (set its name)'}`,
      code: [
        "kubectl apply -f - <<'YAML'",
        'apiVersion: apps/v1',
        'kind: DaemonSet',
        'metadata:',
        `  name: gpudeck-agent-${node}`,
        '  namespace: gpudeck-system',
        'spec:',
        '  selector:',
        `    matchLabels: { app: gpudeck-agent, gpudeck.io/node: "${node}" }`,
        '  template:',
        '    metadata:',
        `      labels: { app: gpudeck-agent, gpudeck.io/node: "${node}" }`,
        '    spec:',
        `      nodeSelector: { kubernetes.io/hostname: "${node}" }`,
        '      runtimeClassName: nvidia',
        '      hostNetwork: true   # agent proxy listens on :9443 for the API layer',
        '      containers:',
        '        - name: agent',
        '          image: ghcr.io/sur950/gpu-deck-agent:1.0.0',
        '          args: ["run"]',
        '          env:',
        '            - name: GPUDECK_AGENT_HUB_URL',
        `              value: "${ctx.hubUrl}"`,
        '            - name: GPUDECK_AGENT_JOIN_TOKEN',
        `              valueFrom: { secretKeyRef: { name: ${secret}, key: token } }`,
        ...(ctx.caFingerprint
          ? [
              '            - name: GPUDECK_AGENT_CA_FINGERPRINT',
              `              valueFrom: { secretKeyRef: { name: ${secret}, key: ca-fingerprint } }`,
            ]
          : []),
        '            - name: NVIDIA_VISIBLE_DEVICES',
        '              value: all',
        ...labelLines,
        '          securityContext: { runAsNonRoot: true, runAsUser: 10001, readOnlyRootFilesystem: true, allowPrivilegeEscalation: false }',
        '          volumeMounts:',
        '            - { name: state, mountPath: /var/lib/gpudeck }',
        '      volumes:',
        '        - name: state',
        '          hostPath: { path: /var/lib/gpudeck, type: DirectoryOrCreate }',
        'YAML',
        `kubectl -n gpudeck-system rollout status ds/gpudeck-agent-${node}`,
      ].join('\n'),
    },
  ];
}

const PLATFORM_ICON = {
  linux: Terminal,
  macos: Apple,
  kubernetes: Container,
} as const;

interface InstallCommandsProps {
  ctx: InstallContext;
  defaultPlatform?: InstallPlatform;
}

/** Copy-paste install + join steps for the three supported service managers. */
export function InstallCommands({
  ctx,
  defaultPlatform = 'linux',
}: InstallCommandsProps) {
  const [platform, setPlatform] = useState<InstallPlatform>(defaultPlatform);
  const steps = {
    linux: linuxSteps(ctx),
    macos: macosSteps(ctx),
    kubernetes: kubernetesSteps(ctx),
  };
  return (
    <Tabs
      value={platform}
      onValueChange={(v) => setPlatform(v as InstallPlatform)}
    >
      <TabsList className="bg-muted">
        {(Object.keys(INSTALL_PLATFORM_LABELS) as InstallPlatform[]).map(
          (p) => {
            const Icon = PLATFORM_ICON[p];
            return (
              <TabsTrigger key={p} value={p} className="px-3 text-xs">
                <Icon className="size-3.5" />
                {INSTALL_PLATFORM_LABELS[p]}
              </TabsTrigger>
            );
          },
        )}
      </TabsList>
      {(Object.keys(steps) as InstallPlatform[]).map((p) => (
        <TabsContent key={p} value={p} className="mt-2 space-y-3">
          {steps[p].map((step) => (
            <div key={step.title} className="space-y-1.5">
              <p className="text-xs font-medium text-muted-foreground">
                {step.title}
              </p>
              <CodeBlock
                code={step.code}
                language={p === 'kubernetes' ? 'shell · yaml' : 'shell'}
                maxHeight={
                  p === 'linux' && step.title.startsWith('3') ? 320 : undefined
                }
              />
            </div>
          ))}
        </TabsContent>
      ))}
    </Tabs>
  );
}
