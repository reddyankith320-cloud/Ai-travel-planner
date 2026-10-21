/**
 * Browser-local persistence.
 *
 * Two things live here: an owner token that scopes the saved-trips list, and
 * a draft of the trip form so a reload does not lose the user's input.
 *
 * Every access is wrapped: `localStorage` throws in private windows and when
 * site data is blocked, and a planner that crashes because storage is
 * unavailable would be a poor trade.
 */

const OWNER_KEY = 'tripwise.owner';
const DRAFT_KEY = 'tripwise.draft';

function readRaw(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeRaw(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage unavailable. The app works for this session regardless.
  }
}

/** Generate an opaque, non-guessable id without pulling in a dependency. */
function randomToken(): string {
  const bytes = new Uint8Array(16);
  if (window.crypto?.getRandomValues) {
    window.crypto.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * The id the saved-trips list is scoped by.
 *
 * This is not a credential and grants no privileges beyond listing trips
 * saved with the same token. Clearing site data starts a fresh list.
 */
export function getOwnerToken(): string {
  const existing = readRaw(OWNER_KEY);
  if (existing && existing.length >= 16) return existing;
  const token = randomToken();
  writeRaw(OWNER_KEY, token);
  return token;
}

export function saveDraft<T>(draft: T): void {
  try {
    writeRaw(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    // Unserialisable draft; nothing to do.
  }
}

export function loadDraft<T>(): T | null {
  const raw = readRaw(DRAFT_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function clearDraft(): void {
  try {
    window.localStorage.removeItem(DRAFT_KEY);
  } catch {
    // Nothing to clean up.
  }
}
