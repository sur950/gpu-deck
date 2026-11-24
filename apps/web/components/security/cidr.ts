/**
 * IPv4 / IPv6 address and CIDR parsing, used to validate IP rules in the
 * form and to evaluate them in the "test an address" checker preview.
 * A bare address is treated as a host route (/32 or /128).
 */

export interface ParsedCidr {
  version: 4 | 6;
  /** Network address as a big integer. */
  network: bigint;
  prefix: number;
  /** Canonical text, e.g. "10.0.0.0/8". */
  canonical: string;
  /** Number of addresses covered (as text, IPv6 ranges are huge). */
  size: string;
  /** True when the host bits were not zero ("10.0.0.5/8" → normalised to 10.0.0.0/8). */
  hadHostBits: boolean;
}

function parseIpv4(text: string): bigint | null {
  const parts = text.split('.');
  if (parts.length !== 4) return null;
  let value = BigInt(0);
  for (const part of parts) {
    if (!/^\d{1,3}$/.test(part)) return null;
    const n = Number(part);
    if (n > 255 || (part.length > 1 && part.startsWith('0'))) return null;
    value = (value << BigInt(8)) + BigInt(n);
  }
  return value;
}

function parseIpv6(text: string): bigint | null {
  let input = text.toLowerCase();
  if (input.includes('%')) return null;
  // Embedded IPv4 tail (::ffff:192.168.1.1).
  const v4Tail = /(\d{1,3}(?:\.\d{1,3}){3})$/.exec(input);
  if (v4Tail) {
    const v4 = parseIpv4(v4Tail[1]);
    if (v4 === null) return null;
    const hi = Number((v4 >> BigInt(16)) & BigInt(0xffff)).toString(16);
    const lo = Number(v4 & BigInt(0xffff)).toString(16);
    input = `${input.slice(0, -v4Tail[1].length)}${hi}:${lo}`;
  }
  const halves = input.split('::');
  if (halves.length > 2) return null;
  const head = halves[0] ? halves[0].split(':') : [];
  const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
  const missing = 8 - head.length - tail.length;
  if (halves.length === 1 && head.length !== 8) return null;
  if (halves.length === 2 && missing < 1) return null;
  const groups = [...head, ...Array.from({ length: halves.length === 2 ? missing : 0 }, () => '0'), ...tail];
  if (groups.length !== 8) return null;
  let value = BigInt(0);
  for (const g of groups) {
    if (!/^[0-9a-f]{1,4}$/.test(g)) return null;
    value = (value << BigInt(16)) + BigInt(parseInt(g, 16));
  }
  return value;
}

export function parseIp(text: string): { version: 4 | 6; value: bigint } | null {
  const t = text.trim();
  if (!t) return null;
  if (t.includes(':')) {
    const v = parseIpv6(t);
    return v === null ? null : { version: 6, value: v };
  }
  const v = parseIpv4(t);
  return v === null ? null : { version: 4, value: v };
}

function formatIpv4(value: bigint): string {
  return [24, 16, 8, 0].map((s) => Number((value >> BigInt(s)) & BigInt(255))).join('.');
}

function formatIpv6(value: bigint): string {
  const groups = Array.from({ length: 8 }, (_, i) => Number((value >> BigInt((7 - i) * 16)) & BigInt(0xffff)));
  // Collapse the longest run of zero groups (RFC 5952).
  let bestStart = -1;
  let bestLen = 0;
  for (let i = 0; i < 8; ) {
    if (groups[i] !== 0) {
      i += 1;
      continue;
    }
    let j = i;
    while (j < 8 && groups[j] === 0) j += 1;
    if (j - i > bestLen && j - i > 1) {
      bestStart = i;
      bestLen = j - i;
    }
    i = j;
  }
  const hex = groups.map((g) => g.toString(16));
  if (bestStart < 0) return hex.join(':');
  const left = hex.slice(0, bestStart).join(':');
  const right = hex.slice(bestStart + bestLen).join(':');
  return `${left}::${right}`;
}

export function parseCidr(text: string): ParsedCidr | null {
  const [addr, prefixText, extra] = text.trim().split('/');
  if (extra !== undefined || !addr) return null;
  const ip = parseIp(addr);
  if (!ip) return null;
  const bits = ip.version === 4 ? 32 : 128;
  let prefix = bits;
  if (prefixText !== undefined) {
    if (!/^\d{1,3}$/.test(prefixText)) return null;
    prefix = Number(prefixText);
    if (prefix > bits) return null;
  }
  const hostBits = BigInt(bits - prefix);
  const mask = ((BigInt(1) << BigInt(bits)) - BigInt(1)) ^ ((BigInt(1) << hostBits) - BigInt(1));
  const network = ip.value & mask;
  const canonicalAddr = ip.version === 4 ? formatIpv4(network) : formatIpv6(network);
  const count = BigInt(1) << hostBits;
  return {
    version: ip.version,
    network,
    prefix,
    canonical: `${canonicalAddr}/${prefix}`,
    size: count > BigInt(1_000_000_000) ? `2^${bits - prefix}` : count.toLocaleString(),
    hadHostBits: network !== ip.value,
  };
}

/** Whether `ip` falls inside `cidr` (false for mixed versions or bad input). */
export function cidrContains(cidr: string, ip: string): boolean {
  const c = parseCidr(cidr);
  const a = parseIp(ip);
  if (!c || !a || c.version !== a.version) return false;
  const bits = c.version === 4 ? 32 : 128;
  const hostBits = BigInt(bits - c.prefix);
  return a.value >> hostBits === c.network >> hostBits;
}

/** Human validation message for a CIDR field, or null when valid. */
export function cidrError(text: string): string | null {
  if (!text.trim()) return 'Enter an address or range, e.g. 10.0.0.0/8';
  const c = parseCidr(text);
  if (!c) return 'Not a valid IPv4 / IPv6 address or CIDR range';
  return null;
}
