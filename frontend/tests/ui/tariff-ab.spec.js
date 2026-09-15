import { test, expect } from '@playwright/test';

test.describe('TariffView A→B 任意两国速查', () => {
  test('panel renders with CN→VN defaults', async ({ page }) => {
    await page.goto('/tariff');
    const panel = page.locator('.panel-card').filter({ hasText: '任意两国进口关税速查' });
    await expect(panel).toBeVisible();
    await expect(panel.locator('button:has-text("查询税率")')).toBeVisible();
  });

  test('CN→VN HS 61091010 returns 30%', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/tariff');
    const panel = page.locator('.panel-card').filter({ hasText: '任意两国进口关税速查' });
    await panel.locator('input[placeholder*="输入 HS 编码"]').pressSequentially('61091010', { delay: 80 });
    await page.waitForTimeout(300);
    await panel.locator('button:has-text("查询税率")').click();
    await expect(panel.locator('.ab-rate')).toHaveText('30%', { timeout: 30_000 });
    await expect(panel.locator('.ab-provenance')).toContainText('CN → VN');
    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  });
});
