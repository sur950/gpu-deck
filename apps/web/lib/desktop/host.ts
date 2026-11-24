/**
 * Desktop host detection. Inside the GPU Deck desktop app (Tauri, remote-shell
 * mode) the shell injects a frozen `window.__GPUDECK_DESKTOP__` object into the
 * connected server's pages; it exposes exactly two calls, `notify` and
 * `setTrayStatus`. In a normal browser the object does not exist and every
 * function here is a no-op that resolves to `false`.
 *
 * Auth never crosses this boundary: the shell gets display text and in-app
 * paths only; the session stays in the webview's cookies.
 */

export type DesktopSeverity = 'info' | 'warning' | 'critical';

export interface DesktopNotification {
  title: string;
  body?: string;
  /** In-app path opened when the notification is clicked (e.g. `/swaps/<id>`). */
  path?: string;
  severity?: DesktopSeverity;
}

export interface DesktopTrayItem {
  label: string;
  path?: string;
}

export interface DesktopTrayStatus {
  health: 'ok' | 'degraded' | 'down' | 'unknown';
  nodesOnline?: number;
  nodesTotal?: number;
  devicesUnhealthy?: number;
  deploymentsRunning?: number;
  swapsInProgress?: number;
  alertsOpen?: number;
  activeSwaps: DesktopTrayItem[];
  openAlerts: DesktopTrayItem[];
}

export interface DesktopHost {
  version: 1;
  platform: string;
  appVersion?: string;
  notify(notification: DesktopNotification): Promise<void>;
  setTrayStatus(status: DesktopTrayStatus): Promise<void>;
}

declare global {
  interface Window {
    __GPUDECK_DESKTOP__?: unknown;
  }
}

/** The desktop host, or null in a normal browser (and during SSR). */
export function getDesktopHost(): DesktopHost | null {
  if (typeof window === 'undefined') return null;
  const host = window.__GPUDECK_DESKTOP__ as Partial<DesktopHost> | undefined;
  if (
    !host ||
    typeof host !== 'object' ||
    host.version !== 1 ||
    typeof host.notify !== 'function' ||
    typeof host.setTrayStatus !== 'function'
  ) {
    return null;
  }
  return host as DesktopHost;
}

export function isDesktopShell(): boolean {
  return getDesktopHost() !== null;
}

/** Show a native notification. Resolves false outside the desktop app or on refusal. */
export async function desktopNotify(notification: DesktopNotification): Promise<boolean> {
  const host = getDesktopHost();
  if (!host) return false;
  try {
    await host.notify(notification);
    return true;
  } catch {
    return false;
  }
}

/** Update the tray icon and menu. Resolves false outside the desktop app or on refusal. */
export async function desktopSetTrayStatus(status: DesktopTrayStatus): Promise<boolean> {
  const host = getDesktopHost();
  if (!host) return false;
  try {
    await host.setTrayStatus(status);
    return true;
  } catch {
    return false;
  }
}
