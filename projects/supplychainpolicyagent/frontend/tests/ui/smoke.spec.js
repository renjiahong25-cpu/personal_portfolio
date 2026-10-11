import { test, expect } from '@playwright/test';

const routes = ['/', '/chat', '/knowledge', '/spider', '/eval', '/tariff', '/quote'];

test.describe('smoke: all views mount without JS errors', () => {
  for (const r of routes) {
    test(`${r}`, async ({ page }) => {
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e.message)));
      await page.goto(r);
      await page.waitForSelector('#app[data-v-app]', { timeout: 30_000 });
      await page.waitForTimeout(2000);
      expect(errors, 'no uncaught JS errors').toHaveLength(0);
    });
  }
});
