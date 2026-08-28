/** RTL: queue rendering + filter logic (issue #39) — API mocked at the
 * transport boundary with realistic fixtures, no network. */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TestProviders } from "~/test/providers";
import { ExceptionQueuePage } from "./ExceptionQueuePage";

const QUEUE_PAGE = {
  items: [
    {
      invoice_id: 41,
      invoice_number: "INV-1001",
      status: "EXCEPTION",
      vendor_id: 3,
      currency: "EUR",
      amount_total: "1100.00",
      issue_date: "2026-09-01",
      created_at: "2026-09-01T12:00:00+00:00",
      run: { run_id: 9, route: "EXCEPTION", status: "AWAITING_DECISION", confidence: null },
      exception: {
        exception_id: 5,
        type: "PRICE_MM",
        severity: "HIGH",
        status: "OPEN",
        assignee: "maria@invoiceops",
        sla_due_at: "2026-09-02T12:00:00+00:00",
        sla_overdue_seconds: 9000,
      },
    },
    {
      invoice_id: 40,
      invoice_number: "INV-1000",
      status: "EXCEPTION",
      vendor_id: 2,
      currency: "EUR",
      amount_total: "300.00",
      issue_date: "2026-09-01",
      created_at: "2026-09-01T11:00:00+00:00",
      run: { run_id: 8, route: "EXCEPTION", status: "AWAITING_DECISION", confidence: null },
      exception: {
        exception_id: 4,
        type: "MISSING_PO",
        severity: "MEDIUM",
        status: "OPEN",
        assignee: null,
        sla_due_at: "2026-09-03T12:00:00+00:00",
        sla_overdue_seconds: -3600,
      },
    },
  ],
  total: 2,
  limit: 50,
  offset: 0,
};

function stubQueue() {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      calls.push(url);
      return new Response(JSON.stringify(QUEUE_PAGE), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return { calls: () => calls };
}

function renderQueue(initial = "/queue") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <TestProviders>
        <Routes>
          <Route path="/queue" element={<ExceptionQueuePage />} />
          <Route path="/queue/:invoiceId" element={<div>detail-view</div>} />
        </Routes>
      </TestProviders>
    </MemoryRouter>,
  );
}

describe("ExceptionQueuePage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders rows with type badges, aging, and drill-through navigation", async () => {
    stubQueue();
    renderQueue();

    expect(await screen.findByText("INV-1001")).toBeInTheDocument();
    expect(screen.getAllByText("PRICE MM").length).toBeGreaterThan(0); // badge + filter option
    expect(screen.getAllByText("MISSING PO").length).toBeGreaterThan(0);
    expect(screen.getByText(/3h/)).toBeInTheDocument(); // 9000s → rounded hours overdue
    expect(screen.getByText("on track")).toBeInTheDocument();
    expect(screen.getByText("maria@invoiceops")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByText("INV-1001"));
    expect(await screen.findByText("detail-view")).toBeInTheDocument();
  });

  it("applies filters as server-side query params", async () => {
    const { calls } = stubQueue();
    renderQueue("/queue?exception_type=PRICE_MM&sort=severity");

    await waitFor(() => expect(screen.getByText("INV-1001")).toBeInTheDocument());
    const queueCall = calls().find((url) => url.includes("/v1/invoices"));
    expect(queueCall).toBeDefined();
    expect(queueCall!).toContain("exception_type=PRICE_MM");
    expect(queueCall!).toContain("sort=severity");
    expect(queueCall!).toContain("order=asc");
  });

  it("shows the empty state for a clear queue", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );
    renderQueue();
    expect(
      await screen.findByText("No exceptions match — the queue is clear."),
    ).toBeInTheDocument();
  });
});
