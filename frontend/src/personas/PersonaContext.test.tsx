/** Persona switcher: switching changes the identity headers the API checks. */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TestProviders } from "~/test/providers";
import { PersonaSwitcher } from "~/components/PersonaSwitcher";
import { usePersona } from "~/personas/PersonaContext";

function HeaderProbe() {
  const { headers } = usePersona();
  return <div data-testid="probe">{JSON.stringify(headers)}</div>;
}

describe("PersonaSwitcher", () => {
  it("defaults to Maria (analyst) and switching personas changes the RBAC headers", async () => {
    render(
      <TestProviders>
        <HeaderProbe />
        <PersonaSwitcher />
      </TestProviders>,
    );

    const probe = screen.getByTestId("probe");
    expect(probe.textContent).toContain("maria@invoiceops");
    expect(probe.textContent).toContain("analyst");
    expect(screen.getByTestId("persona-role")).toHaveTextContent("analyst");

    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: "Priya" }));

    expect(probe.textContent).toContain("priya@invoiceops");
    expect(probe.textContent).toContain("audit");
    expect(window.localStorage.getItem("invoiceops.persona")).toBe("priya");
  });
});
