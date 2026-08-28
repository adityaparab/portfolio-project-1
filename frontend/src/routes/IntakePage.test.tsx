/** RTL: intake navigates to the live run on accepted uploads; duplicates
 * stay on the intake page with their REJECTED feedback. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TestProviders } from "~/test/providers";
import { IntakePage } from "./IntakePage";

function FileStub(props: Partial<File> = {}): File {
  return new File(["%PDF-1.4 intake-test"], "a.pdf", {
    type: "application/pdf",
    ...props,
  }) as File;
}

function stubUpload(duplicate: boolean) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const bodyOf = async (): Promise<string> => {
        if (typeof init?.body === "string") return init.body;
        if (input instanceof Request) return await input.clone().text();
        return "";
      };
      await bodyOf(); // drain (upload payload) — deterministic stub
      const hash = "d".repeat(64);
      const receipt = duplicate
        ? { invoice_id: 3, run_id: 8, content_hash: hash, status: "REJECTED", duplicate: true }
        : { invoice_id: 3, run_id: 8, content_hash: hash, status: "RECEIVED", duplicate: false };
      return new Response(JSON.stringify(receipt).replace('"dddd"', `"${"d".repeat(64)}"`), {
        status: duplicate ? 200 : 201,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

async function uploadFlow() {
  const user = userEvent.setup();
  const input = document.querySelector('input[type="file"]') as HTMLInputElement | null;
  fireEvent.change(input!, { target: { files: [FileStub()] } });
  await user.click(screen.getByTestId("intake-submit"));
  return user;
}

describe("IntakePage navigation", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  });
  afterEach(() => vi.unstubAllGlobals());

  it("navigates to /runs/{run_id} when the upload is accepted", async () => {
    stubUpload(false);
    render(
      <MemoryRouter initialEntries={["/intake"]}>
        <TestProviders>
          <Routes>
            <Route path="/intake" element={<IntakePage />} />
            <Route path="/runs/:runId" element={<div>run-view-8</div>} />
          </Routes>
        </TestProviders>
      </MemoryRouter>,
    );

    await uploadFlow();
    await waitFor(() => expect(screen.getByText("run-view-8")).toBeInTheDocument());
  });

  it("keeps duplicates on the intake page with REJECTED feedback", async () => {
    stubUpload(true);
    render(
      <MemoryRouter initialEntries={["/intake"]}>
        <TestProviders>
          <Routes>
            <Route path="/intake" element={<IntakePage />} />
            <Route path="/runs/:runId" element={<div>run-view-8</div>} />
          </Routes>
        </TestProviders>
      </MemoryRouter>,
    );

    await uploadFlow();
    await waitFor(() => expect(screen.getByText(/REJECTED/)).toBeInTheDocument());
    expect(screen.queryByText("run-view-8")).toBeNull();
    expect(window.location.pathname).toBe("/"); // memory router base — not /runs
  });
});
