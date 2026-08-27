/** RTL: decision form validation + four-eyes confirm step (issue #32 AC). */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TestProviders } from "~/test/providers";
import { ExceptionDetailPage } from "./ExceptionDetailPage";

/** Detail aggregate fixture — realistic pipeline shape ( PRICE_MM exception). */
const DETAIL = {
  invoice: {
    invoice_id: 7,
    invoice_number: "INV-MM-1",
    status: "EXCEPTION",
    vendor_id: 2,
    currency: "EUR",
    amount_total: "1100.00",
    issue_date: "2026-09-01",
    created_at: "2026-09-01T12:00:00+00:00",
    run: { run_id: 3, route: "EXCEPTION", status: "AWAITING_DECISION", confidence: null },
    exception: {
      exception_id: 11,
      type: "PRICE_MM",
      severity: "HIGH",
      status: "OPEN",
      assignee: "maria@invoiceops",
      sla_due_at: "2026-09-02T12:00:00+00:00",
      sla_overdue_seconds: 0,
    },
  },
  lines: [
    {
      line_no: "1",
      description: "Widget",
      qty: "10",
      uom: "EA",
      unit_price: "110.00",
      tax_code: "S",
      line_total: "1100.00",
    },
  ],
  extraction: {
    vendor_name: "Acme",
    invoice_number: "INV-MM-1",
    po_number: "PO-2026-00001",
    issue_date: "2026-09-01",
    due_date: null,
    currency: "EUR",
    total_amount: "1100.00",
    tax_total: "209.00",
    iban: null,
    lines: [],
    confidences: { total_amount: 0.98, vendor_name: 0.97 },
    min_confidence: 0.97,
  },
  validation: [],
  match: {
    outcome: "MISMATCH",
    findings: [
      {
        code: "PRICE_MM",
        severity: "ERROR",
        detail: "line 1: unit price 110.00 exceeds PO 100.00 by +10.00 (band 2)",
        line_no: "1",
        delta: { invoice: "110.00", po: "100.00", delta: "10.00", band: "2" },
      },
    ],
  },
  policy: [],
  gate: null,
  exception: {
    exception_id: 11,
    type: "PRICE_MM",
    severity: "HIGH",
    status: "OPEN",
    evidence: { findings: [] },
    recommendation: {
      classification: "PRICE_MM",
      confidence: 0.92,
      abstained: false,
      suggested_action: "ESCALATE",
      recommendation: "Confirm the 10% increase was agreed.",
      rationale: "Beyond the 2% band.",
    },
  },
  ledger: { entry_count: 5, last_entries: [] },
  state_available: true,
};

function renderDetail() {
  let decisionCall: { url: string; body: unknown } | null = null;
  const urls: string[] = [];
  const fetchStub = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    urls.push(url);
    const bodyOf = async (): Promise<unknown> => {
      if (typeof init?.body === "string") return JSON.parse(init.body);
      if (input instanceof Request) {
        try {
          return await input.clone().json();
        } catch {
          return null;
        }
      }
      return null;
    };
    if (url.includes("/v1/invoices/7")) {
      return Promise.resolve(
        new Response(JSON.stringify(DETAIL), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }
    if (url.includes("/decision")) {
      decisionCall = { url, body: await bodyOf() };
      return Promise.resolve(
        new Response(
          JSON.stringify({
            decision_id: 91,
            exception_id: 11,
            invoice_id: 7,
            action: "APPROVE",
            actor: "dan@invoiceops",
            reason_code: "PRICE_TOLERATED",
            created_at: "2026-09-01T13:00:00+00:00",
            exception_status: "RESOLVED",
            graph_resumed: true,
            idempotent_replay: false,
          }),
          { status: 201, headers: { "content-type": "application/json" } },
        ),
      );
    }
    return Promise.resolve(
      new Response(JSON.stringify({ items: [], total: 0, limit: 50, offset: 0 }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
  });
  vi.stubGlobal("fetch", fetchStub);
  return { decisionCalls: () => decisionCall, urls: () => urls };
}

describe("ExceptionDetailPage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders deltas, confidences and the triage recommendation; the form enforces validation", async () => {
    renderDetail();
    render(
      <MemoryRouter initialEntries={["/queue/7"]}>
        <TestProviders>
          <Routes>
            <Route path="/queue/:invoiceId" element={<DetailStub />} />
          </Routes>
        </TestProviders>
      </MemoryRouter>,
    );

    // deterministic findings + exact deltas visible
    expect(await screen.findByText(/unit price 110\.00 exceeds PO 100\.00/)).toBeInTheDocument();
    expect(screen.getByText(/invoice=110\.00/)).toBeInTheDocument();
    // per-field confidences
    expect(screen.getByText("0.98")).toBeInTheDocument();
    // triage recommendation rendered
    expect(screen.getByText(/Confirm the 10% increase/)).toBeInTheDocument();

    const user = userEvent.setup();
    // validation: empty rationale blocks submit with the Zod message
    await user.click(screen.getByTestId("decision-submit"));
    expect(
      await screen.findByText(/Rationale is required for the audit trail/),
    ).toBeInTheDocument();

    // fill and open the four-eyes confirm step
    await user.type(screen.getByLabelText("Rationale (audited)"), "Agreed increase on file.");
    await user.type(screen.getByLabelText("Reason code"), "PRICE_TOLERATED");
    await user.click(screen.getByTestId("decision-submit"));
    expect(await screen.findByText("Four-eyes rule")).toBeInTheDocument();
    expect(screen.getByTestId("confirm-decision")).toBeInTheDocument();
  });

  it("submits the decision after confirmation and returns to the queue", async () => {
    const { decisionCalls } = renderDetail();
    render(
      <MemoryRouter initialEntries={["/queue/7"]}>
        <TestProviders>
          <Routes>
            <Route path="/queue/:invoiceId" element={<DetailStub />} />
            <Route path="/queue" element={<div>queue-view</div>} />
          </Routes>
        </TestProviders>
      </MemoryRouter>,
    );

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Rationale (audited)"), "Agreed increase on file.");
    await user.type(screen.getByLabelText("Reason code"), "PRICE_TOLERATED");
    await user.click(screen.getByTestId("decision-submit"));
    await user.click(await screen.findByTestId("confirm-decision"));

    await waitFor(() => expect(screen.getByText("queue-view")).toBeInTheDocument());
    const call = decisionCalls();
    expect(call).not.toBeNull();
    expect(call?.body).toMatchObject({
      action: "APPROVE",
      reason_code: "PRICE_TOLERATED",
      rationale: "Agreed increase on file.",
    });
  });
});

function DetailStub() {
  return <ExceptionDetailPage />;
}
