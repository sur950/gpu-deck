/**
 * Browser download helpers for CSV / JSON exports.
 *
 * `downloadFromApi` streams an authenticated export endpoint (`text/csv`
 * attachment) through `fetch` with the session cookie and saves it under
 * the suggested file name; `downloadText` saves client-built content.
 */

import { errorFromResponse, readJson, sendRequest } from '@/lib/api/client';

export function downloadText(content: string, filename: string, mime = 'text/csv;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime });
  downloadBlob(blob, filename);
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/** Fetch an export endpoint (relative to NEXT_PUBLIC_API_URL) and save the attachment. */
export async function downloadFromApi(path: string, fallbackFilename: string): Promise<void> {
  const response = await sendRequest('GET', path);
  if (!response.ok) throw errorFromResponse(response.status, await readJson(response));

  const disposition = response.headers.get('content-disposition') ?? '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const filename = match?.[1] ? decodeURIComponent(match[1]) : fallbackFilename;
  downloadBlob(await response.blob(), filename);
}

/** CSV cell escaping (RFC 4180). */
export function csvCell(value: unknown): string {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Build a CSV document from column definitions and rows. */
export function toCsv<T>(rows: T[], columns: { header: string; value: (row: T) => unknown }[]): string {
  const lines = [columns.map((c) => csvCell(c.header)).join(',')];
  for (const row of rows) lines.push(columns.map((c) => csvCell(c.value(row))).join(','));
  return lines.join('\n');
}
