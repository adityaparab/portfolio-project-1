/** RTL: Agent Run master/detail — vertical stepper renders the run's path,
 * clicking a step selects it, detail shows the recorded model call (reasoning
 * + output) or the pending status. Transport-mocked; no network. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TestProviders } from "~/test/providers";
import { AgentRunPage } from "./AgentRunPage";

const TRACE = {
  run_id: 5,
  invoice_id: 9,
  status: "AWAITING_DECISION",
  route: "EXCEPTION",
  confidence: 0.61,
  graph_version: "0.1.0",
  node_trace: ["ingest", "extract", "validate", "match3way", "policy", "gate", "exception_triage"],
  active_node: null,
  stage_models: {
    extract: { alias: "extract-vision", wire_model: "glm-ocr" },
    triage: { alias: "triage-reasoner", wire_model: "gemma4" },
    policy: { alias: "embed", wire_model: "nomic-embed" },
    prompt_versions: { extract: "extract@v3", triage: "triage@v2" },
  },
  timeline: [
    { seq: 1, actor_type: "SYSTEM", actor_id: "ingest", event: "run.started", payload: {}, created_at: "2026-09-01T12:00:00Z" },
    { seq: 2, actor_type: "AGENT", actor_id: "extract", event: "extract.completed", payload: { min_confidence: 0.9 }, created_at: "2026-09-01T12:00:01Z" },
    { seq: 3, actor_type: "SYSTEM", actor_id: "validate", event: "validate.completed", payload: { passed: true }, created_at: "2026-09-01T12:00:02Z" },
    { seq: 4, actor_type: "SYSTEM", actor_id: "match3way", event: "match.completed", payload: { outcome: "MISMATCH" }, created_at: "2026-09-01T12:00:03Z" },
    { seq: 5, actor_type: "POLICY", actor_id: "policy", event: "policy.evaluated", payload: { passed: false }, created_at: "2026-09-01T12:00:04Z" },
  ],
};

const MODEL_CALLS = [
  {
    call_id: 11,
    run_id: 5,
    invoice_id: 9,
    stage: "extract",
    alias: "extract-vision",
    wire_model: "glm-ocr",
    prompt_version: "extract@v3",
    status: "COMPLETED",
    reasoning_text: "I should read the vendor block first…",
    output_text: '{"vendor_name": "Arnold Ltd", "total_amount": 466.8}',
    latency_ms: 599,
    created_at: "2026-09-01T12:00:01Z",
  },
];

function stub() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      const body = url.includes("model-calls") ? MODEL_CALLS : TRACE;
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    }),
  );
}

async function loadRun() {
  render(
    <TestProviders>
      <AgentRunPage />
    </TestProviders>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "5");
  return user;
}

describe("AgentRunPage (master/detail stepper)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders the exception path in the stepper and inspects the extract model call", async () => {
    stub();
    const user = await loadRun();

    // Exception path steps visible; the AUTO-branch step is not
    expect(await screen.findByTestId("step-ingest")).toBeInTheDocument();
    expect(screen.getByTestId("step-extract")).toBeInTheDocument();
    expect(screen.getByTestId("step-human_review")).toBeInTheDocument();
    expect(screen.queryByTestId("step-auto_approve")).toBeNull();

    // Detail prompt before selection
    expect(screen.getByText("Select a step to inspect its output.")).toBeInTheDocument();

    // Click Extract -> detail shows model call with reasoning + output
    await user.click(screen.getByTestId("step-extract"));
    const detail = screen.getByTestId("detail-extract");
    expect(detail).toBeInTheDocument();
    expect(screen.getAllByText("glm-ocr").length).toBeGreaterThan(0); // badge + stepper description
    expect(screen.getByText(/extract@v3/)).toBeInTheDocument();
    expect(screen.getByText("Reasoning / thinking")).toBeInTheDocument();
    expect(screen.getByText(/I should read the vendor block first/)).toBeInTheDocument();
    expect(screen.getByText(/Arnold Ltd/)).toBeInTheDocument();
    expect(screen.getByText(/Ledger — extract\.completed/)).toBeInTheDocument();

    // A pending step shows its status without model output
    await user.click(screen.getByTestId("step-human_review"));
    expect(screen.getByTestId("detail-human_review")).toBeInTheDocument();
    expect(screen.getByText(/Not started on this run/)).toBeInTheDocument();
  });

  it("shows the run meta without a live badge when paused", async () => {
    stub();
    await loadRun();
    expect(await screen.findByText("run #5")).toBeInTheDocument();
    expect(screen.getByText("AWAITING_DECISION")).toBeInTheDocument();
    expect(screen.queryByTestId("active-badge")).toBeNull();
  });
});
