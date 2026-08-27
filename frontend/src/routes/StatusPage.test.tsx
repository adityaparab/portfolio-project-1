/** Status page: generated client round-trip + observable persona headers. */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TestProviders } from "~/test/providers";
import { StatusPage } from "./StatusPage";

const READY_BODY = {
  status: "degraded",
  checks: [
    { name: "postgres", ok: true, detail: null },
    { name: "minio", ok: true, detail: null },
    { name: "litellm", ok: false, detail: "ConnectError" },
  ],
};

describe("StatusPage", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input);
        const body = url.includes("readyz") ? READY_BODY : { status: "ok" };
        return Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("renders API + dependency checks through the generated client", async () => {
    render(
      <MemoryRouter>
        <TestProviders>
          <StatusPage />
        </TestProviders>
      </MemoryRouter>,
    );

    await waitFor(
      () => {
        expect(screen.getByText("postgres")).toBeInTheDocument();
      },
      { timeout: 2500 },
    );
    expect(screen.getByText("minio")).toBeInTheDocument();
    expect(screen.getByText("litellm")).toBeInTheDocument();

    const identity = screen.getByTestId("identity-headers");
    expect(identity).toHaveTextContent("X-IO-User: maria@invoiceops");
    expect(identity).toHaveTextContent("X-IO-Role: analyst");
  });
});
