import { test, expect } from '@playwright/test';

const landPct = (page) =>
  page.evaluate(() => {
    const c = document.querySelector('canvas');
    if (!c) return 0;
    const ctx = c.getContext('2d');
    const { data } = ctx.getImageData(0, 0, c.width, c.height);
    let land = 0;
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i], g = data[i + 1], b = data[i + 2];
      if (Math.abs(r - 232) <= 12 && Math.abs(g - 238) <= 12 && Math.abs(b - 247) <= 12) land++;
    }
    return Math.round((100 * land) / (data.length / 4));
  });

test.describe('QuoteView 多中转路径报价', () => {
  test('panel renders', async ({ page }) => {
    await page.goto('/quote');
    const panel = page.locator('.panel-card').filter({ hasText: '多中转路径报价' });
    await expect(panel).toBeVisible();
    await expect(panel.locator('button:has-text("全部路径对比")')).toBeVisible();
  });

  test('全部路径对比 → ECharts 世界地图 + ranked/legs 表 + 到岸成本', async ({ page }) => {
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/quote');
    const panel = page.locator('.panel-card').filter({ hasText: '多中转路径报价' });
    await panel.locator('input[placeholder*="输入商品描述"]').first().pressSequentially('26寸电动摩托车', { delay: 80 });
    const val = panel.locator('.el-input-number input').first();
    await val.click();
    await val.pressSequentially('1000', { delay: 80 });
    await page.keyboard.press('Tab');
    await page.waitForTimeout(300);
    await panel.locator('button:has-text("全部路径对比")').click();

    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 90_000 });
    await expect.poll(() => landPct(page), { timeout: 30_000, intervals: [2000] }).toBeGreaterThan(5);

    await expect(page.locator('.route-key').first()).toBeVisible({ timeout: 30_000 });
    expect(await page.locator('.route-key').count()).toBeGreaterThanOrEqual(3);
    expect(await page.locator('.el-table__row').count()).toBeGreaterThanOrEqual(3);
    await expect(page.locator('text=到岸成本').first()).toBeVisible();

    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  }, 180_000);
});
