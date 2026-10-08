import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";
import path from "node:path";

const routes = [
  ["/", "overview"],
  ["/semantic-transactions", "semantic-transactions"],
  ["/product", "product"],
  ["/integrate", "integrate"],
  ["/compare", "compare"],
  ["/docs", "docs"],
  ["/faq", "faq"],
  ["/get-started", "get-started"],
] as const;

const screenshots = path.resolve(__dirname, "../../../docs/frontend-redesign/screenshots");
const captureScreenshots = process.env.PACT_CAPTURE_SCREENSHOTS === "1";

for (const [label, width, height] of [["desktop", 1440, 900], ["tablet", 820, 1180], ["mobile", 390, 844]] as const) {
  test(`${label} public routes render without horizontal page overflow`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    if (captureScreenshots) mkdirSync(screenshots, { recursive: true });
    await page.setViewportSize({ width, height });
    for (const [route, name] of routes) {
      const response = await page.goto(route);
      expect(response?.status(), route).toBe(200);
      await expect(page.locator("h1").first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth), route).toBeLessThanOrEqual(width + 1);
      if (captureScreenshots) await page.screenshot({ path: path.join(screenshots, `${name}-${label}.png`), fullPage: true });
    }
    expect(pageErrors).toEqual([]);
  });
}

test("public navigation and interactive content work", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await page.getByRole("navigation", { name: "Site" }).getByRole("link", { name: "Integrate" }).click();
  await expect(page).toHaveURL(/\/integrate$/);
  await page.getByRole("tab", { name: "Python client" }).click();
  await expect(page.getByRole("tabpanel")).toContainText("HttpPactClient");
  await page.getByRole("button", { name: "Copy code" }).click();
  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("HttpPactClient");
  await page.goto("/faq");
  await page.getByText("Can PACT reverse every external action?").click();
  await expect(page.getByText(/Compensation depends on the effect contract/)).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Toggle navigation" }).click();
  await expect(page.getByRole("navigation", { name: "Site" }).getByRole("link", { name: "Docs" })).toBeVisible();
  await page.getByRole("navigation", { name: "Site" }).getByRole("link", { name: "Docs" }).click();
  await expect(page).toHaveURL(/\/docs$/);
  await expect(page.getByRole("button", { name: "Toggle navigation" })).toHaveAttribute("aria-expanded", "false");
});

test("internal links from every public page resolve", async ({ page }) => {
  const targets = new Set<string>();
  for (const [route] of routes) {
    await page.goto(route);
    for (const href of await page.locator('a[href^="/"]').evaluateAll((links) =>
      links.map((link) => link.getAttribute("href")).filter((href): href is string => Boolean(href)))) {
      targets.add(href.split("#")[0]);
    }
  }
  for (const href of targets) {
    const response = await page.request.get(href);
    expect(response.status(), href).toBe(200);
  }
});
