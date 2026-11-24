/**
 * OpenSSH public-key helpers for the SSH GPU instance form.
 *
 * Mirrors the backend's validation (apps/fastapi/app/services/gpu_instances/keys.py):
 * one `<type> <base64> [comment]` per line, no `from=`/`command=` options, the
 * blob's embedded type must match, duplicates collapse by fingerprint. The
 * fingerprint is OpenSSH's `SHA256:<unpadded base64>` so users can compare it
 * with `ssh-keygen -lf key.pub`.
 */

export const SSH_KEY_TYPES = [
  'ssh-ed25519',
  'ssh-rsa',
  'ecdsa-sha2-nistp256',
  'ecdsa-sha2-nistp384',
  'ecdsa-sha2-nistp521',
  'sk-ssh-ed25519@openssh.com',
  'sk-ecdsa-sha2-nistp256@openssh.com',
] as const;

export const MAX_SSH_KEYS = 64;

export interface ParsedSshKey {
  /** Normalised line: `<type> <base64> [comment]`. */
  line: string;
  type: string;
  comment: string | null;
  /** Decoded key blob (fingerprint input). */
  blob: Uint8Array;
}

export type SshKeyResult = { ok: true; key: ParsedSshKey } | { ok: false; error: string };

function decodeBase64(b64: string): Uint8Array | null {
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(b64) || b64.length % 4 !== 0) return null;
  try {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

/** Validate one authorized_keys line. */
export function parseSshKey(raw: string): SshKeyResult {
  const text = raw.trim();
  if (!text) return { ok: false, error: 'Empty line' };
  if (/^-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/.test(text)) {
    return { ok: false, error: 'This is a private key — remove it and paste the public key (.pub) instead' };
  }
  const parts = text.split(/\s+/);
  const [type, b64, ...rest] = parts;
  if (!(SSH_KEY_TYPES as readonly string[]).includes(type)) {
    return { ok: false, error: type.includes('=') ? 'Key options (from=, command=…) are not allowed' : `Unsupported key type "${type.slice(0, 40)}"` };
  }
  if (!b64) return { ok: false, error: 'Missing the key data after the type' };
  const blob = decodeBase64(b64);
  if (!blob || blob.length < 4) return { ok: false, error: 'Key data is not valid base64' };
  const nameLength = new DataView(blob.buffer, blob.byteOffset, blob.byteLength).getUint32(0);
  const embedded = new TextDecoder().decode(blob.subarray(4, 4 + nameLength));
  if (embedded !== type) return { ok: false, error: `Key data says "${embedded.slice(0, 40)}" but the line says "${type}"` };
  const comment = rest.join(' ').trim() || null;
  return { ok: true, key: { line: comment ? `${type} ${b64} ${comment}` : `${type} ${b64}`, type, comment, blob } };
}

/** OpenSSH `SHA256:` fingerprint of a key blob (Web Crypto). */
export async function sshFingerprint(blob: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', blob as BufferSource));
  let bin = '';
  for (const byte of digest) bin += String.fromCharCode(byte);
  return `SHA256:${btoa(bin).replace(/=+$/, '')}`;
}

export interface KeyLine {
  /** 1-based line number in the textarea. */
  lineNo: number;
  result: SshKeyResult;
}

/** Every non-blank, non-comment line of a textarea / .pub file. */
export function parseSshKeyLines(text: string): KeyLine[] {
  return text
    .split(/\r?\n/)
    .map((line, i) => ({ line, lineNo: i + 1 }))
    .filter(({ line }) => line.trim() && !line.trimStart().startsWith('#'))
    .map(({ line, lineNo }) => ({ lineNo, result: parseSshKey(line) }));
}

/** Short type label for chips: "ED25519", "RSA", "ECDSA-256", "SK-ED25519". */
export function sshKeyTypeLabel(type: string): string {
  if (type === 'ssh-ed25519') return 'ED25519';
  if (type === 'ssh-rsa') return 'RSA';
  if (type.startsWith('ecdsa-sha2-nistp')) return `ECDSA-${type.slice('ecdsa-sha2-nistp'.length)}`;
  if (type.startsWith('sk-ssh-ed25519')) return 'SK-ED25519';
  if (type.startsWith('sk-ecdsa')) return 'SK-ECDSA';
  return type.toUpperCase();
}
