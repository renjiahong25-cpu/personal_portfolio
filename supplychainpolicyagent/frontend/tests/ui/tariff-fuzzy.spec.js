import { test, expect } from '@playwright/test';

test.describe('TariffView A→B 商品名模糊搜索', () => {
  test('人偶 → 候选表 → 点选 95030021 → 展示税率 15%', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/tariff');
    const panel = page.locator('.panel-card').filter({ hasText: '任意两国进口关税速查' });
    await panel.locator('input[placeholder*="HS 编码或商品名"]').pressSequentially('人偶', { delay: 80 });
    await page.waitForTimeout(300);
    await panel.locator('button:has-text("查询税率")').click();

    await expect(panel.locator('.ab-cand-wrap')).toBeVisible({ timeout: 30_000 });
    const rows = panel.locator('.ab-cand-clickable');
    expect(await rows.count()).toBeGreaterThanOrEqual(2);
    await expect(rows.first()).toContainText('95030021');
    await rows.first().click();

    await expect(panel.locator('.ab-result')).toBeVisible({ timeout: 30_000 });
    await expect(panel.locator('.ab-rate')).toHaveText('15%');
    await expect(panel.locator('.ab-provenance')).toContainText('CN → VN');
    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  }, 90_000);

  test('精确 HS 码路径不受影响：61091010 → 30%', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/tariff');
    const panel = page.locator('.panel-card').filter({ hasText: '任意两国进口关税速查' });
    await panel.locator('input[placeholder*="HS 编码或商品名"]').pressSequentially('61091010', { delay: 80 });
    await page.waitForTimeout(300);
    await panel.locator('button:has-text("查询税率")').click();

    await expect(panel.locator('.ab-result')).toBeVisible({ timeout: 30_000 });
    await expect(panel.locator('.ab-rate')).toHaveText('30%', { timeout: 30_000 });
    await expect(panel.locator('.ab-provenance')).toContainText('CN → VN');
    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  }, 90_000);
});