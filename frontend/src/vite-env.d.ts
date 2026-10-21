/// <reference types="vite/client" />

/**
 * Typed frontend environment.
 *
 * Only values that are safe in a browser belong here. Everything in a VITE_*
 * variable is compiled into the bundle and is readable by anyone who loads
 * the page, so no private API key may ever appear in this interface.
 */
interface ImportMetaEnv {
  /** Base URL of the TripWise API. Empty in dev, where Vite proxies /api. */
  readonly VITE_API_BASE_URL?: string;
  /**
   * A browser-restricted map key, if you swap OpenStreetMap tiles for a
   * commercial provider. Public by design; it must be locked to your domain
   * by HTTP referrer in the provider's console.
   */
  readonly VITE_GOOGLE_MAPS_BROWSER_KEY?: string;
  /** '1' in the static preview build, which replays recorded results. */
  readonly VITE_PREVIEW?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
