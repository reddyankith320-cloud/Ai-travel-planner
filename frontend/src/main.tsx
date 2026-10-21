import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App';
import { IS_PREVIEW } from './lib/mode';
import './styles/global.css';

if (IS_PREVIEW) {
  // The preview is a single static page. Leaflet's stylesheet is bundled in
  // rather than fetched from a CDN, and /api calls are answered from recorded
  // results because there is no server to ask.
  await import('leaflet/dist/leaflet.css');
  const { installPreviewShim } = await import('./preview/shim');
  installPreviewShim();
}

// A static page has no server to rewrite deep links, so the preview routes by
// URL fragment instead of by path.
const Router = IS_PREVIEW ? HashRouter : BrowserRouter;

const container = document.getElementById('root');
if (!container) throw new Error('Root element #root is missing from index.html');

createRoot(container).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
);
