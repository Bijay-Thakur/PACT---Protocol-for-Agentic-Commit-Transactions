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
  await page.goto("/requests/new");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${fixture.customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await expect(page.getByLabel("Customer ID")).toHaveValue(fixture.customer);
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  await expect(page.getByText("The frozen plan is ready for a separate authorized approver.")).toBeVisible();
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
  await page.goto("/requests/new");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  await expect(page.getByText("The frozen plan is ready for a separate authorized approver.")).toBeVisible();
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
  await page.goto("/requests/new");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${customer} and refund the unused period`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("REVIEWABLE_REQUEST")).toBeVisible();
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  await expect(page.getByText("The frozen plan is ready for a separate authorized approver.")).toBeVisible();
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

test("contradictory intent stays in clarification until the issue is explicitly resolved", async ({ page }) => {
  const customer = `${fixture.customer}-CONTRADICT`;
  await page.goto("/requests/new");
  await signIn(page, "requester");
  await page.getByLabel("Business intent").fill(`Cancel customer ${customer} but also keep it active`);
  await page.getByRole("button", { name: "Interpret and review" }).click();
  await expect(page.getByText("NEEDS_CLARIFICATION")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /CONTRADICTORY_OUTCOME/ })).toBeVisible();
  await page.getByRole("checkbox", { name: /CONTRADICTORY_OUTCOME/ }).check();
  await page.getByLabel("Clarified objective").fill(`Cancel ${customer} and refund the unused period`);
  await page.getByLabel("Clarification note (required if the proposal has unresolved questions)").fill(
    "The customer confirmed cancellation; the keep-active clause is withdrawn");
  await page.getByRole("button", { name: "Accept reviewed request into a draft" }).click();
  await page.getByRole("button", { name: "Assemble actions from trusted facts" }).click();
  await page.getByRole("button", { name: "Prepare and freeze consequence review" }).click();
  await expect(page.getByText("The frozen plan is ready for a separate authorized approver.")).toBeVisible();
});

test("paused UNKNOWN demo reconciles from the incident without a second refund", async ({ page }) => {
  await page.goto("/demo");
  await signIn(page, "approver");
  await page.getByLabel("Live pacing").selectOption("0");
  await page.getByRole("checkbox", { name: "Pause at UNKNOWN" }).check();
  await page.locator(".neu-raised-md", { hasText: "Ambiguous result" }).getByRole("button", { name: "Run" }).click();
  await expect(page).toHaveURL(/\/tx\//);
  await expect(page.getByText("UNKNOWN").first()).toBeVisible({ timeout: 60_000 });
  const txUrl = page.url();
  await page.goto("/incidents");
  await expect(page.getByText("UNKNOWN").first()).toBeVisible();
  await page.goto(txUrl);
  await page.getByRole("button", { name: "Reconcile" }).click();
  await expect(page.getByText("COMMITTED_VERIFIED").first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole("link", { name: /View receipt/ }).click();
  await expect(page.getByText("COMMITTED_VERIFIED").first()).toBeVisible();
});

test("primary navigation is keyboard reachable at desktop and mobile widths", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/transactions");
  await signIn(page, "requester");
  const incidents = page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Incidents" });
  await incidents.focus();
  await expect(incidents).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/incidents/);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("link", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("UNKNOWN, mismatch, and residual obligations requiring action")).toBeVisible();
});
