/**
 * Copy-paste install steps: GPU machines talk only to the agent hub (pinned CA), and a
 * multi-use enrolment token yields one DaemonSet for the whole pool.
 */
import { describe, expect, it } from 'vitest';
import { downloadSteps, kubernetesSteps, linuxSteps, type InstallContext } from '@/components/fleet/install-commands';

const ctx: InstallContext = {
  hubUrl: 'https://hub.example.com:8443',
  token: 'gdj_abc',
  caFingerprint: 'ff'.repeat(32),
  nodeName: null,
  labels: { pool: 'a' },
  downloadBase: 'https://hub.example.com:8443/agent/v1',
  caPemSha256: 'ab'.repeat(32),
  version: '1.0.0',
};

describe('install steps', () => {
  it('download from the hub with the pinned CA, never the admin API', () => {
    const steps = downloadSteps(ctx, 'x86_64-unknown-linux-gnu', 'sha256sum').join('\n');
    expect(steps).toContain('https://hub.example.com:8443/agent/v1/ca.pem');
    expect(steps).toContain(`${'ab'.repeat(32)}  gpudeck-ca.pem`);
    expect(steps).toContain('--cacert gpudeck-ca.pem');
    expect(steps).toContain('/agent/v1/downloads/1.0.0/x86_64-unknown-linux-gnu');
    expect(linuxSteps(ctx)[0].code).not.toContain('/api/v1');
  });

  it('uses the system trust store when no CA pin is known', () => {
    const steps = downloadSteps({ ...ctx, caPemSha256: null }, 'aarch64-apple-darwin', 'shasum -a 256').join('\n');
    expect(steps).not.toContain('--cacert');
  });

  it('gives a pool-wide DaemonSet for a multi-use token and a per-node one otherwise', () => {
    const pool = kubernetesSteps({ ...ctx, multiUse: true }).map((s) => s.code).join('\n');
    expect(pool).toContain('nodeAgent.joinTokenSecret=gpudeck-agent-join');
    expect(pool).toContain("--set-string 'nodeAgent.labels.pool=a'");
    const single = kubernetesSteps({ ...ctx, nodeName: 'gpu-1' }).map((s) => s.code).join('\n');
    expect(single).toContain('gpudeck-agent-gpu-1');
  });
});
