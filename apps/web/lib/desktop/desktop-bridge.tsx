/**
 * Desktop bridge: mounted once in the signed-in shell. Renders nothing and
 * does nothing in a normal browser. Inside the GPU Deck desktop app it
 *
 * - forwards the live events the app already receives (`alert`, `rule_run`,
 *   `swap` on `/live/events`) and new items in the `/metrics/overview`
 *   snapshot (same query the dashboard polls) to native notifications;
 * - keeps the tray icon / menu in sync (fleet health, active swaps, open alerts);
 * - handles `gpudeck:navigate` (notification / tray clicks) with the Next router.
 */

'use client';

import { useEffect, useRef, useSyncExternalStore } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getOverview } from '@/lib/api/overview';
import { overviewKeys } from '@/hooks/use-overview';
import { useLiveEvents, type LiveTopic } from '@/hooks/use-live-events';
import { desktopNotify, desktopSetTrayStatus, isDesktopShell } from '@/lib/desktop/host';
import { isAppPath, Tracker, trayStatusFromOverview } from '@/lib/desktop/events';

const TOPICS: LiveTopic[] = ['alert', 'rule_run', 'swap'];

// The host object is injected before any page script runs and never changes.
const subscribe = () => () => {};

export function DesktopBridge() {
  // false on the server and in browsers; true only inside the desktop app.
  const enabled = useSyncExternalStore(subscribe, isDesktopShell, () => false);
  return enabled ? <ActiveBridge /> : null;
}

function ActiveBridge() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const tracker = useRef(new Tracker());
  const lastTray = useRef('');

  const overview = useQuery({
    queryKey: overviewKeys.snapshot(),
    queryFn: getOverview,
    refetchInterval: 30_000,
    refetchIntervalInBackground: true,
    staleTime: 10_000,
  });

  useLiveEvents({
    topics: TOPICS,
    onEvent: (event) => {
      const notification = tracker.current.fromLive(event);
      if (notification) void desktopNotify(notification);
      // Swaps / alerts change the counts behind the tray: refresh now.
      void queryClient.invalidateQueries({ queryKey: overviewKeys.snapshot() });
    },
  });

  useEffect(() => {
    if (!overview.data) return;
    for (const notification of tracker.current.fromOverview(overview.data)) void desktopNotify(notification);
  }, [overview.data]);

  useEffect(() => {
    const reachable = !(overview.isError && !overview.data);
    const status = trayStatusFromOverview(overview.data, reachable);
    const key = JSON.stringify(status);
    if (key === lastTray.current) return;
    lastTray.current = key;
    void desktopSetTrayStatus(status);
  }, [overview.data, overview.isError]);

  useEffect(() => {
    const onNavigate = (e: Event) => {
      const path = (e as CustomEvent<{ path?: unknown }>).detail?.path;
      if (!isAppPath(path)) return;
      e.preventDefault();
      router.push(path);
    };
    window.addEventListener('gpudeck:navigate', onNavigate);
    return () => window.removeEventListener('gpudeck:navigate', onNavigate);
  }, [router]);

  return null;
}
