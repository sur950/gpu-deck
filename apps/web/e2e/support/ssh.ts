import { generateKeyPairSync } from 'node:crypto';

/** A throwaway OpenSSH ed25519 public key line (`ssh-ed25519 AAAA… comment`). */
export function sshEd25519PublicKey(comment = 'e2e@gpu-deck'): string {
  const { publicKey } = generateKeyPairSync('ed25519');
  const raw = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32);
  const str = (b: Buffer) => Buffer.concat([Buffer.from([0, 0, 0, b.length]), b]);
  const blob = Buffer.concat([str(Buffer.from('ssh-ed25519')), str(raw)]);
  return `ssh-ed25519 ${blob.toString('base64')} ${comment}`;
}
