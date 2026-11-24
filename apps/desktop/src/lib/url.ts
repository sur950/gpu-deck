/**
 * Server URL rules for instant feedback in the connect form. The Rust side
 * (src-tauri/src/servers.rs) applies the same rules and is authoritative.
 *
 * - https:// is the default when no scheme is typed.
 * - http:// only for localhost, *.localhost, *.local and loopback / private /
 *   link-local / CGNAT / IPv6 ULA addresses, and it is flagged `insecure`.
 * - No credentials, path, query or fragment: a server is an origin.
 */

export type UrlCheck =
  | { ok: true; origin: string; insecure: boolean; host: string }
  | { ok: false; error: string };

function parseV4(host: string): number[] | null {
  const parts = host.split('.');
  if (parts.length !== 4) return null;
  const nums = parts.map((p) => (/^\d{1,3}$/.test(p) ? Number(p) : NaN));
  return nums.every((n) => n >= 0 && n <= 255) ? nums : null;
}

function isPrivateV4([a, b]: number[]): boolean {
  if (a === undefined || b === undefined) return false;
  return (
    a === 127 ||
    a === 10 ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    (a === 169 && b === 254) ||
    (a === 100 && b >= 64 && b <= 127)
  );
}

function isPrivateV6(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '').toLowerCase();
  if (h === '::1') return true;
  const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(h);
  if (mapped?.[1]) {
    const v4 = parseV4(mapped[1]);
    return v4 ? isPrivateV4(v4) : false;
  }
  const first = parseInt(h.split(':')[0] || '0', 16);
  if (Number.isNaN(first)) return false;
  return (first & 0xfe00) === 0xfc00 || (first & 0xffc0) === 0xfe80;
}

/** Hosts where plain http is tolerated (with a warning). */
export function isLocalOrPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, '');
  if (host.startsWith('[') || host.includes(':')) return isPrivateV6(host);
  const v4 = parseV4(host);
  if (v4) return isPrivateV4(v4);
  return (
    host === 'localhost' ||
    host.endsWith('.localhost') ||
    host.endsWith('.local')
  );
}

export function normalizeServerUrl(input: string): UrlCheck {
  const raw = input.trim();
  if (!raw) return { ok: false, error: 'Enter the server URL.' };
  if (raw.length > 2048) return { ok: false, error: 'The URL is too long.' };
  if (/[\s\u0000-\u001f\u007f]/.test(raw)) {
    return {
      ok: false,
      error: 'Not a valid URL (contains spaces or control characters).',
    };
  }
  const withScheme = raw.includes('://') ? raw : `https://${raw}`;
  let url: URL;
  try {
    url = new URL(withScheme);
  } catch {
    return { ok: false, error: 'Not a valid URL.' };
  }
  const scheme = url.protocol.replace(/:$/, '');
  if (scheme !== 'https' && scheme !== 'http') {
    return {
      ok: false,
      error: `Only https:// (or http:// for local addresses) is allowed, not ${scheme}://.`,
    };
  }
  if (url.username || url.password) {
    return {
      ok: false,
      error:
        'Do not put a username or password in the URL; sign in inside the app.',
    };
  }
  if (!url.hostname)
    return { ok: false, error: 'Not a valid URL (missing host).' };
  if (
    (url.pathname && url.pathname !== '/') ||
    url.search ||
    url.hash ||
    /[?#]/.test(raw)
  ) {
    return {
      ok: false,
      error: "Use the server's base address only (no path, query or fragment).",
    };
  }
  const insecure = scheme === 'http';
  if (insecure && !isLocalOrPrivateHost(url.hostname)) {
    return {
      ok: false,
      error: `http:// is only allowed for localhost and private network addresses; use https://${url.host}.`,
    };
  }
  return { ok: true, origin: url.origin, insecure, host: url.host };
}
