/**
 * Copy-paste install steps: GPU machines talk only to the agent hub (pinned CA), and a
 * multi-use enrolment token yields one DaemonSet for the whole pool.
 */
import { describe, expect, it } from 'vitest';
import {
  downloadSteps,
  joinOptionFlags,
  kubernetesSteps,
  linuxSteps,
  macosSteps,
  withInstallOptions,
  type InstallContext,
} from '@/components/fleet/install-commands';

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

  it('defaults to the outbound tunnel with the host firewall applied and verified', () => {
    const linux = linuxSteps(ctx).map((s) => s.code).join('\n');
    expect(linux).not.toContain('--serving-mode');
    expect(linux).toContain('firewall apply');
    expect(linux).toContain('firewall status');
    expect(linux).toContain('AmbientCapabilities=CAP_NET_ADMIN');
    expect(macosSteps(ctx).map((s) => s.code).join('\n')).toContain('sudo /usr/local/bin/gpudeck-agent firewall apply');
  });

  it('adds the direct mode and the firewall opt-out to every command', () => {
    const linux = linuxSteps({ ...ctx, servingMode: 'direct', firewall: false }).map((s) => s.code).join('\n');
    expect(linux).toContain('--serving-mode direct');
    expect(linux).toContain('--no-firewall');
    expect(linux).not.toContain('firewall apply');
    expect(linux).not.toContain('CAP_NET_ADMIN');
    expect(joinOptionFlags()).toEqual([]);
    const one = "sh -c 'curl -fsSL -H \"X-GPUDeck-Join-Token: gdj_x\" https://hub:8443/agent/v1/install.sh | sh'";
    expect(withInstallOptions(one)).toBe(one);
    expect(withInstallOptions(one, 'direct')).toContain('/agent/v1/install.sh?mode=direct |');
    expect(withInstallOptions(one, 'tunnel', false)).toContain('/agent/v1/install.sh?firewall=0 |');
    expect(withInstallOptions(one, 'direct', false)).toContain('install.sh?mode=direct&firewall=0');
  });
});
