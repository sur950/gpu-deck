import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

const SESSION_COOKIE = process.env.NEXT_PUBLIC_SESSION_COOKIE || 'gpudeck_session';

// Routes that do NOT require auth. GPU Deck has no landing page, sign-up,
// forgot-password or SSO callback: only the sign-in page is public.
const PUBLIC_PREFIX_PATHS = ['/login'];

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIX_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

// Static assets served from public/ (branding, favicon). Matched by extension
// only — a bare "contains a dot" check would let any page path with a dot in
// it (e.g. a model name like qwen3-0.6b) skip the guard.
const STATIC_FILE = /\.(?:svg|png|jpe?g|gif|webp|avif|ico|txt|xml|webmanifest|woff2?|ttf|map)$/i;

/** True for paths the session guard never applies to (Next internals, API routes, static files). */
export function isGuardExempt(pathname: string): boolean {
  return (
    pathname.startsWith('/_next/') ||
    // `/api/...` only: `startsWith('/api')` would also exempt the /api-keys pages.
    pathname === '/api' ||
    pathname.startsWith('/api/') ||
    STATIC_FILE.test(pathname)
  );
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Allow public paths
  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  // Allow Next internals, API routes and static files
  if (isGuardExempt(pathname)) {
    return NextResponse.next();
  }

  // Check for session cookie. Presence-only: the backend validates it.
  const session = request.cookies.get(SESSION_COOKIE);
  if (!session?.value) {
    const loginUrl = new URL('/login', request.url);
    if (pathname !== '/') loginUrl.searchParams.set('from', pathname);
    return NextResponse.redirect(loginUrl);
  }

  // No landing page: the root of the signed-in app is the dashboard.
  if (pathname === '/') {
    const url = request.nextUrl.clone();
    url.pathname = '/dashboard';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
