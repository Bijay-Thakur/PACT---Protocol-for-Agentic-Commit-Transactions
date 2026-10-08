import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const screenshots = path.resolve(__dirname, "../../../docs/frontend-redesign/screenshots");
const captureScreenshots = process.env.PACT_CAPTURE_SCREENSHOTS === "1";

test("authenticated console and original Demo Mode remain available", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  if (captureScreenshots) mkdirSync(screenshots, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/demo");
  await page.getByLabel("Tenant").fill("phase21-browser");
  await page.getByLabel("Username").fill("approver");
  await page.getByLabel("Password").fill("phase21-fixture-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Signed in as approver", { exact: true })).toBeVisible();
  await expect(page.getByText("Simulated demo area.")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Demo Mode" })).toBeVisible();
  if (captureScreenshots) await page.screenshot({ path: path.join(screenshots, "demo-desktop.png"), fullPage: true });

  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Overview" }).click();
  await expect(page).toHaveURL(/\/console$/);
  await expect(page.getByText("Signed in as approver", { exact: true })).toBeVisible();
  await expect(page.getByText("Worker backlog")).toBeVisible();
  await expect(page.getByRole("table")).toBeVisible();
  if (captureScreenshots) await page.screenshot({ path: path.join(screenshots, "console-desktop.png"), fullPage: true });
  expect(pageErrors).toEqual([]);
});

test("every original Demo Mode scenario reaches its recorded outcome", async ({ page }) => {
  test.setTimeout(180_000);
  const scenarios = [
    ["success", "COMMITTED_VERIFIED"],
    ["invariant-failure", "ABORTED"],
    ["budget-conflict", "ABORTED"],
    ["unknown", "COMMITTED_VERIFIED"],
    ["compensation", "COMPENSATED"],
    ["compensation-failure", "HUMAN_REQUIRED"],
    ["notification-ordering", "COMMITTED_VERIFIED"],
    ["verification-mismatch", "COMPENSATED"],
    ["duplicate-operation", "ABORTED"],
  ] as const;
  await page.goto("/demo");
  await page.getByLabel("Tenant").fill("phase21-browser");
  await page.getByLabel("Username").fill("approver");
  await page.getByLabel("Password").fill("phase21-fixture-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".neu-raised-md")).toHaveCount(scenarios.length);

  for (const [key, expected] of scenarios) {
    await page.goto("/demo");
    await page.getByLabel("Live pacing").selectOption("0");
    await page.getByRole("checkbox", { name: "Pause at UNKNOWN" }).uncheck();
    const card = page.locator(".neu-raised-md").filter({ has: page.getByText(key, { exact: true }) });
    await expect(card).toHaveCount(1);
    await card.getByRole("button", { name: "Run" }).click();
    await expect(page).toHaveURL(/\/tx\/[0-9a-f-]+$/i);
    await expect(page.getByText(expected, { exact: true }).first(), key).toBeVisible({ timeout: 30_000 });
  }
});
