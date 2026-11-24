import { describe, expect, it } from 'vitest';
import { parseSshKey, parseSshKeyLines, sshFingerprint, sshKeyTypeLabel } from '@/lib/ssh-keys';

// Throwaway public keys; fingerprints taken from `ssh-keygen -lf`.
const ED25519 = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIO0iVzQbxv9so2DZfE1htSnq1ygGfsoJktBMl93qIcAs alice@laptop';
const RSA = 'ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDH5kWIH1yUiS57m9yT+ffanoYmFR9ZF44q9AfpmWLSku1e4VvgOeFvRDfTOHYnX545hVcFlo6PxADxFToKzrwpyIVWFFrygZNcy2bnay0GUy4/S4DrSm7U1q/fN43vTixfofS5b9/IEkVGPUynjDxRrfs1MjfSx1uxJM8ZTVDZ4xvjlh/piHzprZFI78uJQmnsGNCR8Dls1H/oGBDf5c2w/fhQAHprj9biTFHRbArjT0QFtFt7TZuD8sQFv/NH6Fkx05izwPFlHPFojJII/Txp2rsGIFWm0IpSRa83+XCrwiEmCviuMD4hp4lXsAcHWrvaWssMQX7b5GRxMC2SNkoP ci';

describe('parseSshKey', () => {
  it('accepts OpenSSH public keys and keeps the comment', () => {
    const r = parseSshKey(`  ${ED25519}  `);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.key.type).toBe('ssh-ed25519');
      expect(r.key.comment).toBe('alice@laptop');
      expect(r.key.line).toBe(ED25519);
    }
  });

  it('rejects options, unknown types, bad base64 and mismatched blobs', () => {
    expect(parseSshKey(`command="rm -rf /" ${ED25519}`)).toMatchObject({ ok: false, error: expect.stringContaining('not allowed') });
    expect(parseSshKey('ssh-dss AAAA')).toMatchObject({ ok: false, error: expect.stringContaining('Unsupported') });
    expect(parseSshKey('ssh-ed25519 not*base64')).toMatchObject({ ok: false, error: expect.stringContaining('base64') });
    expect(parseSshKey(`ssh-rsa ${ED25519.split(' ')[1]}`)).toMatchObject({ ok: false, error: expect.stringContaining('says') });
    expect(parseSshKey('ssh-ed25519')).toMatchObject({ ok: false });
  });
});

describe('sshFingerprint', () => {
  it('matches ssh-keygen -lf', async () => {
    const a = parseSshKey(ED25519);
    const b = parseSshKey(RSA);
    if (!a.ok || !b.ok) throw new Error('fixture keys must parse');
    expect(await sshFingerprint(a.key.blob)).toBe('SHA256:5JvVw674q17ehFXdsucAhP8xaFbe1No7b/R2cqdv7z4');
    expect(await sshFingerprint(b.key.blob)).toBe('SHA256:Q3dkDxfzH2b5q1UXjZ2ZUJo/0iK5uzmy1tSmFSy/ZJ8');
  });
});

describe('parseSshKeyLines', () => {
  it('skips blanks and comments and reports line numbers', () => {
    const lines = parseSshKeyLines(`# team keys\n${ED25519}\n\nnot a key\n${RSA}\n`);
    expect(lines.map((l) => [l.lineNo, l.result.ok])).toEqual([
      [2, true],
      [4, false],
      [5, true],
    ]);
  });

  it('names a pasted private key instead of reporting an unknown type', () => {
    const [line] = parseSshKeyLines('-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----');
    expect(line.result.ok).toBe(false);
    expect(!line.result.ok && line.result.error).toMatch(/private key/);
  });

  it('labels key types', () => {
    expect(sshKeyTypeLabel('ssh-ed25519')).toBe('ED25519');
    expect(sshKeyTypeLabel('ecdsa-sha2-nistp384')).toBe('ECDSA-384');
    expect(sshKeyTypeLabel('sk-ssh-ed25519@openssh.com')).toBe('SK-ED25519');
  });
});
