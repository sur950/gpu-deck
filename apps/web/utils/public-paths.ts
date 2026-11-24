/**
 * Pages reachable without signing in. GPU Deck has a single seeded admin and
 * no landing page, sign-up or password reset, so only the sign-in page is
 * public. Keep in sync with PUBLIC_PREFIX_PATHS in proxy.ts.
 */
const PUBLIC_PREFIX_PATHS = ['/login'];

export function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIX_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}
