/**
 * Dashboard route template — Next.js remounts a template on navigation
 * between sibling routes (not on query-string or nested-route changes), so it
 * gives each page a short fade-in without resetting nested layout state.
 * h-full keeps the wrapper transparent to pages that fill <main> (logs,
 * audit logs, policy editor use flex h-full layouts).
 */

export default function DashboardTemplate({ children }: { children: React.ReactNode }) {
  return <div className="h-full animate-page-in">{children}</div>;
}
