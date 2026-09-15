import { test, expect } from '@playwright/test';

test.describe('ChatView 对话（依赖远程 LLM）', () => {
  test('发送问题得到 AI 回复', { tag: ['@llm'] }, async ({ page }) => {
    test.setTimeout(1_800_000);
    const errors = [];
    page.on('pageerror', (e) => errors.push(String(e.message)));
    await page.goto('/chat');
    const ta = page.locator('.chat-input textarea, textarea').first();
    await ta.waitFor({ timeout: 30_000 });
    await ta.pressSequentially('跨境电商进口关税一般有哪些合规要求？', { delay: 60 });
    await page.waitForTimeout(300);
    await page.locator('button:has-text("发送")').first().click();

    await expect
      .poll(
        async () => (await page.locator('button:has-text("发送"), button:has-text("生成中")').first().textContent()).trim(),
        { timeout: 1_800_000, intervals: [2000] }
      )
      .toBe('发送');

    const txt = (await page.locator('.message.ai-message').last().innerText()).replace(/有用|有误/g, '').trim();
    expect(txt.length, 'assistant reply non-empty').toBeGreaterThan(5);
    expect(errors, 'no uncaught JS errors').toHaveLength(0);
    await page.waitForTimeout(Number(process.env.PW_HOLD || 0));
  });
});
