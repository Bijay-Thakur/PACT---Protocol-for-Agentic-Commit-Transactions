import { expect, test, type Page } from "@playwright/test";

const fixture = {
  tenant: "phase21-browser",
  password: "phase21-fixture-password",
  customer: process.env.PACT_BROWSER_CUSTOMER_ID || "C-BROWSER-21",
};

async function signIn(page: Page, username: "requester" | "approver") {
  await page.getByLabel("Tenant").fill(fixture.tenant);
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(fixture.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText(`Signed in as ${username}`)).toBeVisible();
}

test("requester accepts an intent, separate operator approves, and exact commit yields a receipt", async ({ page }) => {
  await page.goto("/");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${fixture.customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await expect(page.getByLabel("Customer ID")).toHaveValue(fixture.customer);
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  const draftLink = page.getByRole("link", { name: /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i }).first();
  const href = await draftLink.getAttribute("href");
  expect(href).toMatch(/^\/tx\/[0-9a-f-]+$/i);
  await draftLink.click();
  await expect(page.getByText("Frozen consequence review")).toBeVisible();
  await expect(page.getByRole("button", { name: "Request exact commit" })).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "approver");
  await page.goto(href!);
  await page.getByPlaceholder("Approval reason").fill("Reviewed exact refund and revocation");
  await page.getByRole("button", { name: "Approve frozen plan" }).click();
  await expect(page.getByText(/Approve: /)).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "requester");
  await page.goto(href!);
  await page.getByRole("button", { name: "Request exact commit" }).click();
  await expect(page.getByText(/Request exact commit: /)).toBeVisible();
  await page.getByRole("link", { name: /View receipt/ }).click();
  await expect(page.getByText("COMMITTED_VERIFIED").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("DRAFT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("$143.27").first()).toBeVisible();
  await expect(page.getByText("Final observations")).toBeVisible();
  await page.getByRole("button", { name: "Verify hash" }).click();
  await expect(page.getByText(/valid.*stored.*recomputed/)).toBeVisible();
});

test("revision invalidates approval; denied approval and a browser disconnect do not bypass the barrier", async ({ page }) => {
  const customer = `${fixture.customer}-REVISE`;
  await page.goto("/");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  const draftLink = page.getByRole("link", { name: /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i }).first();
  const href = await draftLink.getAttribute("href");
  expect(href).toMatch(/^\/tx\/[0-9a-f-]+$/i);
  await draftLink.click();
  await expect(page.getByText("Frozen consequence review")).toBeVisible();
  const apiURL = process.env.NEXT_PUBLIC_PACT_API_URL || "http://127.0.0.1:18000";
  const first = await (await page.request.get(`${apiURL}/api/v1/transactions/${href!.split("/").at(-1)}`)).json();
  const oldRevision = first.plan_revision.number;
  await page.getByPlaceholder("Approval reason").fill("Requester is not the approver");
  await page.getByRole("button", { name: "Approve frozen plan" }).click();
  await expect(page.getByText(/Approve failed:/)).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "approver");
  await page.goto(href!);
  await page.getByPlaceholder("Approval reason").fill("Reviewed first frozen plan");
  await page.getByRole("button", { name: "Approve frozen plan" }).click();
  await expect(page.getByText(/Approve: /)).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "requester");
  await page.goto(href!);
  await page.getByPlaceholder("Approval reason").fill("Refresh the frozen revision");
  await page.getByRole("button", { name: "Revise frozen plan" }).click();
  await expect(page.getByRole("button", { name: "Prepare revision" })).toBeVisible();
  await page.getByRole("button", { name: "Prepare revision" }).click();
  await expect(page.getByRole("button", { name: "Request exact commit" })).toBeVisible();
  const second = await (await page.request.get(`${apiURL}/api/v1/transactions/${href!.split("/").at(-1)}`)).json();
  expect(second.plan_revision.number).toBe(oldRevision + 1);
  await page.getByRole("button", { name: "Request exact commit" }).click();
  await expect(page.getByText("Request exact commit: AWAITING_APPROVAL")).toBeVisible();

  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "approver");
  await page.goto(href!);
  await page.getByPlaceholder("Approval reason").fill("Reviewed replacement digest");
  await page.getByRole("button", { name: "Approve frozen plan" }).click();
  await expect(page.getByText(/Approve: /)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "requester");
  await page.goto(href!);
  await page.getByRole("button", { name: "Request exact commit" }).dblclick();
  await expect(page.getByText("Request exact commit: QUEUED")).toBeVisible();
  await page.context().setOffline(true);
  await page.waitForTimeout(1000);
  await page.context().setOffline(false);
  await page.reload();
  await page.getByRole("link", { name: /View receipt/ }).click();
  await expect(page.getByText("COMMITTED_VERIFIED").first()).toBeVisible({ timeout: 60_000 });
});

test("applied refund mismatch remains a human obligation in the browser and draft receipt", async ({ page }) => {
  const customer = `${fixture.customer}-MISMATCH`;
  await page.goto("/");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  const draftLink = page.getByRole("link", { name: /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i }).first();
  const href = await draftLink.getAttribute("href");
  expect(href).toMatch(/^\/tx\/[0-9a-f-]+$/i);
  await draftLink.click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "approver");
  await page.goto(href!);
  await page.getByPlaceholder("Approval reason").fill("Reviewed mismatch fixture plan");
  await page.getByRole("button", { name: "Approve frozen plan" }).click();
  await expect(page.getByText(/Approve: /)).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "requester");
  await page.goto(href!);
  await page.getByRole("button", { name: "Request exact commit" }).click();
  await expect(page.getByText("HUMAN_REQUIRED").first()).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("button", { name: "Retry reconciliation" })).toBeVisible();
  await page.getByRole("link", { name: /View receipt/ }).click();
  await expect(page.getByText("Residual obligations")).toBeVisible();
  await expect(page.getByText("APPLIED_MISMATCH")).toBeVisible();
  await expect(page.getByText(/Observed amount: \$99\.00/).first()).toBeVisible();
});
