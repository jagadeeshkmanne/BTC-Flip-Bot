// 2026-06-10: dashboard times in IST (Asia/Kolkata, UTC+5:30) per user request.
// Bot logs and state.json still store everything in UTC; this is display-only.

const IST = 'Asia/Kolkata';

// "Jun 10, 11:16" — short date + time in IST 24h
export function fmtTradeTime(iso: string | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: IST,
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}

// "Jun 10, 11:16:04 IST" — verbose for position-open lines
export function fmtFullIST(iso: string | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-IN', {
    timeZone: IST,
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  }) + ' IST';
}

// "11:16:04" — clock-only HH:MM:SS in IST
export function fmtClockIST(iso: string | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-IN', {
    timeZone: IST,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: true,
  });
}

// "13:50" — clock HH:MM for target times, e.g. TIME-SL countdown target
export function fmtHourMinIST(date: Date | number): string {
  return new Date(date).toLocaleTimeString('en-IN', {
    timeZone: IST,
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
}

// Given an integer UTC hour (0-23), return the matching IST hour string "HH:00"
// e.g. UTC hour 18 → "23:30 IST" (UTC 18:00 = IST 23:30)
// We return "HH:30" labels for half-hour offsets since IST is UTC+5:30.
export function utcHourToISTLabel(utcHour: number): string {
  // construct a UTC date at midnight today + utcHour:00
  const d = new Date();
  d.setUTCHours(utcHour, 0, 0, 0);
  return d.toLocaleTimeString('en-IN', {
    timeZone: IST,
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  }) + ' IST';
}
