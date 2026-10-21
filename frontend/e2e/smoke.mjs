/**
 * End-to-end smoke test.
 *
 * Drives the real app in Chromium at phone and desktop widths and walks the
 * whole flow: grant geolocation, fill the form, get recommendations, open an
 * itinerary, switch to the budget dashboard. It fails on any console error,
 * any uncaught exception, or any horizontal page overflow.
 *
 * Needs both servers running:
 *   backend:  uvicorn app.main:app --port 8000          (from backend/)
 *   frontend: VITE_API_PROXY_TARGET=http://127.0.0.1:8000 npm run dev -- --port 5199
 *
 * Then:  npm run e2e -- <output-dir-for-screenshots>
 */
import { chromium } from 'playwright';

const BASE = 'http://127.0.0.1:5220';
const OUT = process.argv[2];
const errors = [];

// Honour an explicitly provided browser binary. Useful where a preinstalled
// Chromium does not match the version Playwright would download itself.
const executablePath = process.env.CHROMIUM_PATH || undefined;
const browser = await chromium.launch(executablePath ? { executablePath } : {});

async function session(name, viewport) {
  const context = await browser.newContext({
    viewport,
    geolocation: { latitude: 17.385, longitude: 78.486 },
    permissions: ['geolocation'],
  });
  const page = await context.newPage();
  // Blocked external map tiles are an environment problem, not an app bug,
  // so they are reported separately instead of failing the run.
  const external = [];
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    if (/ERR_TUNNEL_CONNECTION_FAILED|ERR_NAME_NOT_RESOLVED|tile\.openstreetmap|cdnjs/.test(text)) {
      external.push(text);
      return;
    }
    errors.push(`[${name}] console: ${text}`);
  });
  page.on('pageerror', (e) => errors.push(`[${name}] pageerror: ${e.message}`));

  await page.goto(BASE, { waitUntil: 'networkidle' });
  console.log(`[${name}] title: ${await page.title()}`);
  console.log(`[${name}] h1: ${(await page.locator('h1').first().innerText()).slice(0, 70)}`);
  const demo = await page.locator('text=Running free').count();
  console.log(`[${name}] free-mode banner present: ${demo > 0}`);

  // Fill the form. Geolocation is granted, so use the location button.
  await page.click('text=Use my location');
  await page.waitForTimeout(1200);
  await page.fill('#budget', '45000');
  await page.fill('#travelers', '2');
  await page.locator('#days').evaluate((el) => {
    el.value = '5';
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  });
  await page.click('button:has-text("Beaches")');
  await page.click('button:has-text("Heritage")');
  await page.screenshot({ path: `${OUT}/01-setup-${name}.png`, fullPage: true });

  await page.click('button:has-text("Find where I can go")');
  await page.waitForSelector('article.card', { timeout: 60000 });
  const cards = await page.locator('article.card').count();
  console.log(`[${name}] recommendation cards: ${cards}`);
  const firstName = await page.locator('article.card h2').first().innerText();
  const firstTotal = await page.locator('article.card .figure').first().innerText();
  console.log(`[${name}] top: ${firstName} ${firstTotal}`);
  await page.screenshot({ path: `${OUT}/02-recommendations-${name}.png`, fullPage: true });

  // Open the score breakdown to prove the disclosure works.
  await page.locator('button:has-text("How we scored it")').first().click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/03-score-${name}.png`, fullPage: false });

  // Go to the itinerary.
  await page.locator('button:has-text("Plan ")').first().click();
  await page.waitForSelector('section.card h2', { timeout: 60000 });
  await page.waitForTimeout(1500);
  const days = await page.locator('p.eyebrow:has-text("Day ")').count();
  console.log(`[${name}] itinerary day panels: ${days}`);
  await page.screenshot({ path: `${OUT}/04-itinerary-${name}.png`, fullPage: true });

  // Budget dashboard tab.
  await page.click('button:has-text("Budget dashboard")');
  await page.waitForTimeout(600);
  const reconciles = await page.locator('text=matches the total above').count();
  console.log(`[${name}] budget table reconciliation marker: ${reconciles > 0}`);
  await page.screenshot({ path: `${OUT}/05-budget-${name}.png`, fullPage: true });

  // Horizontal overflow check -- the mobile-first claim.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  console.log(`[${name}] horizontal overflow: ${overflow}px`);
  if (overflow > 2) errors.push(`[${name}] page scrolls horizontally by ${overflow}px`);

  if (external.length) {
    console.log(`[${name}] ${external.length} external resource(s) blocked (map tiles/CDN) -- not an app error`);
  }

  await context.close();
}

await session('mobile', { width: 390, height: 844 });
await session('desktop', { width: 1440, height: 900 });

await browser.close();
console.log('\n--- errors ---');
console.log(errors.length ? errors.join('\n') : 'none');
process.exit(errors.length ? 1 : 0);
