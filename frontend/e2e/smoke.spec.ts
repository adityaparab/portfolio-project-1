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
  await page.goto("/intake");
  // Mantine renders a visually-hidden native input behind the button
  const file = page.locator('input[type="file"]');
  await file.setInputFiles({
    name: "smoke-invoice.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4 smoke test invoice"),
  });
  await page.getByTestId("intake-submit").click();

  // Accepted: receipt row appears
  await expect(page.getByText("ACCEPTED")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/invoice #\d+/).first()).toBeVisible();

  // Pipeline runs in the background; without a model backend the run lands
  // FAILED (audited in the ledger + DLQ) — observable via the queue status.
  await page.goto("/queue");
  await expect(page.getByText(/smoke-invoice|#\d+/).first()).toBeVisible({ timeout: 20_000 });
});

test("persona switcher drives the identity headers", async ({ page }) => {
  await page.goto("/status");
  await expect(page.getByTestId("identity-headers")).toContainText("maria@invoiceops");
  await page.getByText("Priya", { exact: true }).click(); // SegmentedControl radios are visually hidden
  await expect(page.getByTestId("identity-headers")).toContainText("priya@invoiceops");
  await expect(page.getByTestId("identity-headers")).toContainText("audit");
});
