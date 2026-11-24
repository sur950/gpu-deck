'use client';

import { useCallback, useSyncExternalStore } from 'react';

/**
 * Per-browser UI preference (sidebar collapse, collapsed sections) kept in
 * localStorage. Never use this for data that must reach the server or other
 * viewers.
 *
 * Storage can be blocked (private windows, cleared site data), so every write
 * also lands in an in-memory copy: the toggle keeps working for the session
 * and simply isn't remembered across reloads.
 */

const CHANGE_EVENT = 'gpu-deck:persisted-string';
const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key) ?? memory.get(key) ?? null;
  } catch {
    return memory.get(key) ?? null;
  }
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener('storage', onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener('storage', onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

export function usePersistedString(key: string): [string | null, (value: string) => void] {
  // Server snapshot is null: the first client render after hydration swaps in
  // the stored value without a hydration mismatch.
  const value = useSyncExternalStore(subscribe, () => read(key), () => null);

  const setValue = useCallback(
    (next: string) => {
      memory.set(key, next);
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // Storage blocked or full: the in-memory copy above still applies.
      }
      window.dispatchEvent(new Event(CHANGE_EVENT));
    },
    [key],
  );

  return [value, setValue];
}
