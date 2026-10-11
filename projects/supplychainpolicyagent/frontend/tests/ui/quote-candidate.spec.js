import { test, expect } from '@playwright/test';

test.describe('QuoteView 泛化商品名 → 候选 → 综合报价', () => {
  test('人偶 → 候选条 → 点选 95030021 → 综合结果区出数', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/quote');
    const panel = page.locator('.panel-card').filter({ hasText: '对美报价成本重算' });
    await panel.locator('input[placeholder*="输入商品描述"]').first().pressSequentially('人偶', { delay: 80 });
    await page.waitForTimeout(300);
    await panel.locator('button:has-text("重算报价")').click();

    const strip = panel.locator('.cand-strip');
    await expect(strip).toBeVisible({ timeout: 30_000 });
    expect(await strip.locator('.el-button').count()).toBeGreaterThanOrEqual(2);
    await strip.locator('.el-button:has-text("95030021")').first().click();

    const res = page.locator('.panel-card').filter({ hasText: '当前路径 ·' });
    await expect(res).toBeVisible({ timeout: 60_000 });
    await expect(res.locator('text=HS 95030021').first()).toBeVisible();
    const total = res.locator('.comp-cell.comp-total:has-text("综合关税") .comp-pct');
    await expect(total).toHaveText(/\d+(\.\d+)?%/);
    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  }, 120_000);
});