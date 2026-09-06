import fs from 'node:fs';
import path from 'node:path';

/**
 * Build-time reads of the published archive.
 *
 * Every number on the site comes through here, from files the pipeline wrote.
 * Nothing is hardcoded and nothing is fetched at request time — the homepage
 * counters in particular are generated from the actual archive, because a
 * hardcoded "187 days" is a lie with a short shelf life.
 */

const DATA_DIR = path.resolve('public/data');

export interface ArchiveIndex {
  generated_at: string;
  pipeline_version: string;
  synthetic: boolean;
  underlying: string;
  latest_date: string;
  n_days: number;
  dates: string[];
  contract_days: number;
  history: Record<string, number | boolean | string | null>[];
}

function readJSON<T>(file: string): T | null {
  try {
    return JSON.parse(fs.readFileSync(path.join(DATA_DIR, file), 'utf8')) as T;
  } catch {
    return null;
  }
}

export const getIndex = () => readJSON<ArchiveIndex>('index.json');
export const getLatest = () => readJSON<any>('latest.json');
export const getDay = (date: string) => readJSON<any>(`archive/${date}.json`);

/** "4:20 PM ET" from an ISO snapshot timestamp. */
export function formatSnapshotET(iso: string | undefined): string {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date(iso)) + ' ET';
  } catch {
    return '—';
  }
}

export const int = (n: number | null | undefined) =>
  n == null || !Number.isFinite(n) ? '—' : Math.round(n).toLocaleString('en-US');

/** Volatility as a percentage, e.g. 0.1521 -> "15.21". */
export const pct = (v: number | null | undefined, dp = 2) =>
  v == null || !Number.isFinite(v) ? '—' : (v * 100).toFixed(dp);

export const num = (v: number | null | undefined, dp = 3) =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

/** Compact dollar magnitude for gamma exposure. */
export function money(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return '—';
  const sign = v < 0 ? '−' : '';
  const a = Math.abs(v);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}bn`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}m`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}k`;
  return `${sign}$${a.toFixed(0)}`;
}
