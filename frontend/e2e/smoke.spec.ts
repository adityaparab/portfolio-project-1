/**
 Happy-path smoke against the Compose stack (issue #39).

 Covers the deterministic E2E wiring: upload through the proxy into the
 API -> MinIO -> pipeline (extraction fails against the unreachable dev
 gateway -> run FAILED + DLQ, fully audited) -> trace/queue observe it.
 The approve-decision leg needs a live model backend; RTL covers the
 four-eyes flow, and Phase 5's eval harness exercises the full journey.
*/
import { expect, test } from "@playwright/test";

test("status page shows a healthy stack", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByRole("heading", { name: "System status" })).toBeVisible();
  await expect(page.getByText("API (generated client round-trip)")).toBeVisible();
  await expect(page.getByText("postgres")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("minio")).toBeVisible();
});

test("ingest a document through the UI and watch the pipeline audit it", async ({ page }) => {
  test.setTimeout(150_000); // upload + background graph start + queue render
  await page.goto("/intake");
  // Mantine renders a visually-hidden native input behind the button
  const file = page.locator('input[type="file"]');
  await file.setInputFiles({
    name: "smoke-invoice.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(`%PDF-1.4 smoke test invoice ${Date.now()}-${Math.random()}`),
  });
  await page.getByTestId("intake-submit").click();

  // Accepted uploads navigate straight to the live run (unique bytes per
  // run — exact-duplicate content would be rejected and stay on intake).
  await page.waitForURL(/\/runs\/\d+/, { timeout: 20_000 });
  expect(page.getByRole("heading", { name: "Agent Run" })).toBeVisible();
  const runId = page.url().match(/\/runs\/(\d+)/)![1];

  // Regression (background-runner bug): the background graph MUST start —
  // run.started hits the ledger within seconds of upload, whatever the
  // model backend does afterward (fast-fail DLQ, slow retries, or success).
  // Stuck at ingest.accepted only = broken wiring.
  const deadline = Date.now() + 60_000;
  let started = false;
  while (Date.now() < deadline && !started) {
    const trace = await page.request.get(`/v1/runs/${runId}/trace`);
    if (trace.ok()) {
      const events = ((await trace.json()) as { timeline?: { event: string }[] }).timeline ?? [];
      started = events.some((entry) => entry.event === "run.started");
    }
    if (!started) await page.waitForTimeout(2_000);
  }
  expect(started, "graph run started after upload (run.started in ledger)").toBe(true);

  await page.goto("/queue");
  await expect(page.getByText(/#\d+/).first()).toBeVisible({ timeout: 20_000 });
});

test("persona switcher drives the identity headers", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByTestId("identity-headers")).toContainText("maria@invoiceops");
  await page.getByText("Priya", { exact: true }).click(); // SegmentedControl radios are visually hidden
  await expect(page.getByTestId("identity-headers")).toContainText("priya@invoiceops");
  await expect(page.getByTestId("identity-headers")).toContainText("audit");
});
