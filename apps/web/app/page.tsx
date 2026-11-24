import { redirect } from 'next/navigation';

/**
 * No public landing page: the root is the signed-in dashboard. proxy.ts
 * already redirects here based on the session cookie; this covers any
 * render path that bypasses it.
 */
export default function RootPage() {
  redirect('/dashboard');
}
