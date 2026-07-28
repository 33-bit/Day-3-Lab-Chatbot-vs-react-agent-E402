import { expect, test } from "@playwright/test";

test("baseline chat and ReAct agent use real backend states", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto("/");

  await expect(page.getByText("Backend sẵn sàng")).toBeVisible();
  await expect(page.getByLabel("Chọn model provider")).toContainText("Offline Mock");
  await expect(page.getByText("search_order", { exact: true })).toBeVisible();

  const input = page.getByLabel("Tin nhắn của bạn");
  await input.fill("Xin chào Order Agent");
  await page.getByLabel("Gửi tin nhắn").click();

  await expect(page.getByText(/Mock Provider/)).toBeVisible();
  await expect(page.getByText("Baseline LLM response")).toBeVisible();

  await page.getByRole("button", { name: /ReAct Agent/ }).click();
  await expect(page.getByText(/ReAct có thể gọi tool/)).toBeVisible();
  const mutationConsent = page.getByLabel("Cho phép tạo yêu cầu đổi/trả");
  await mutationConsent.check();
  await input.fill("Tra cứu đơn DH001 với số điện thoại 0901234567");
  await page.getByLabel("Gửi tin nhắn").click();

  await expect(page.getByText(/Đã tra cứu DH001 thành công/)).toBeVisible();
  await expect(mutationConsent).not.toBeChecked();
  await expect(page.getByText("Action: search_order")).toBeVisible();
  await expect(page.getByText("Observation: search_order")).toBeVisible();
  await expect(page.getByText(/Thought:/)).toHaveCount(0);

  await page.getByLabel("Dùng nền tối").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(pageErrors).toEqual([]);
});

test("mobile navigation and trace drawers are operable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("Backend sẵn sàng")).toBeAttached();

  const sidebar = page.locator(".sidebar");
  await page.getByLabel("Mở thanh điều hướng").click();
  await expect(sidebar).toHaveClass(/mobile-open/);
  await page.getByRole("button", { name: /ReAct Agent/ }).click();
  await expect(sidebar).not.toHaveClass(/mobile-open/);

  const tracePanel = page.locator(".trace-panel");
  await page.getByLabel("Xem chi tiết phiên").click();
  await expect(tracePanel).toHaveClass(/mobile-open/);
  await page.getByLabel("Đóng chi tiết phiên").click();
  await expect(tracePanel).not.toHaveClass(/mobile-open/);
});
