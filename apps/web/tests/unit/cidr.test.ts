import { describe, expect, it } from 'vitest';
import { cidrContains, cidrError, parseCidr, parseIp } from '@/components/security/cidr';

describe('parseIp', () => {
  it('parses IPv4 and rejects leading zeros / out-of-range octets', () => {
    expect(parseIp('10.0.0.1')).toEqual({ version: 4, value: BigInt(0x0a000001) });
    expect(parseIp('10.0.0.256')).toBeNull();
    expect(parseIp('10.0.0.01')).toBeNull();
    expect(parseIp('10.0.0')).toBeNull();
    expect(parseIp('')).toBeNull();
  });

  it('parses IPv6 including :: compression and an embedded IPv4 tail', () => {
    expect(parseIp('::1')?.value).toBe(BigInt(1));
    expect(parseIp('2001:db8::')?.version).toBe(6);
    expect(parseIp('::ffff:192.168.1.1')?.value).toBe((BigInt(0xffff) << BigInt(32)) + BigInt(0xc0a80101));
    expect(parseIp('1::2::3')).toBeNull();
    expect(parseIp('fe80::1%eth0')).toBeNull();
  });
});

describe('parseCidr', () => {
  it('canonicalises and flags host bits', () => {
    const c = parseCidr('10.0.0.5/8');
    expect(c?.canonical).toBe('10.0.0.0/8');
    expect(c?.hadHostBits).toBe(true);
    expect(c?.size).toBe((16_777_216).toLocaleString());
  });

  it('treats a bare address as a host route', () => {
    expect(parseCidr('192.168.1.10')?.canonical).toBe('192.168.1.10/32');
    expect(parseCidr('2001:db8::1')?.canonical).toBe('2001:db8::1/128');
  });

  it('formats IPv6 networks with RFC 5952 zero compression and huge sizes as powers of two', () => {
    const c = parseCidr('2001:db8:0:0:1::/48');
    expect(c?.canonical).toBe('2001:db8::/48');
    expect(c?.size).toBe('2^80');
  });

  it('rejects malformed input', () => {
    expect(parseCidr('10.0.0.0/33')).toBeNull();
    expect(parseCidr('10.0.0.0/8/1')).toBeNull();
    expect(parseCidr('10.0.0.0/x')).toBeNull();
    expect(parseCidr('/8')).toBeNull();
  });
});

describe('cidrContains / cidrError', () => {
  it('matches addresses inside a range of the same family only', () => {
    expect(cidrContains('10.0.0.0/8', '10.20.4.17')).toBe(true);
    expect(cidrContains('10.0.0.0/8', '11.0.0.1')).toBe(false);
    expect(cidrContains('0.0.0.0/0', '203.0.113.9')).toBe(true);
    expect(cidrContains('::/0', '10.0.0.1')).toBe(false);
    expect(cidrContains('2001:db8::/32', '2001:db8:ffff::1')).toBe(true);
    expect(cidrContains('bad', '10.0.0.1')).toBe(false);
  });

  it('explains what is wrong with a field value', () => {
    expect(cidrError('')).toMatch(/Enter an address/);
    expect(cidrError('300.1.1.1')).toMatch(/Not a valid/);
    expect(cidrError('172.16.0.0/12')).toBeNull();
  });
});
