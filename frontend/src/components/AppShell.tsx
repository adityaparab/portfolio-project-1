/** App shell: header nav + persona switcher; the six screens live inside. */
import { AppShell as MantineShell, NavLink, Text } from "@mantine/core";
import { Link, NavLink as RouterNavLink, Outlet } from "react-router-dom";
import { PersonaSwitcher } from "~/components/PersonaSwitcher";
import classes from "./AppShell.module.css";

const NAV = [
  { to: "/", label: "Dashboard", issue: "#33" },
  { to: "/queue", label: "Exception Review", issue: "#32" },
  { to: "/intake", label: "Intake", issue: "#34" },
  { to: "/runs", label: "Agent Run", issue: "#36" },
  { to: "/audit", label: "Audit & Trace", issue: "#37" },
  { to: "/evals", label: "Evals", issue: "#38" },
  { to: "/status", label: "System Status", issue: null },
];

export function AppShell() {
  return (
    <MantineShell header={{ height: 56 }} padding="md" classNames={{ root: classes.root }}>
      <MantineShell.Header classNames={{ header: classes.header }}>
        <Link to="/" className={classes.brand}>
          InvoiceOps
        </Link>
        <nav className={classes.nav}>
          {NAV.map((item) => (
            <RouterNavLink key={item.to} to={item.to} className={classes.navLink}>
              {({ isActive }) => (
                <NavLink label={item.label} active={isActive} variant="light" />
              )}
            </RouterNavLink>
          ))}
        </nav>
        <PersonaSwitcher />
      </MantineShell.Header>
      <MantineShell.Main>
        <Outlet />
      </MantineShell.Main>
      <Text className={classes.footer}>deterministic controls at the edges · LLMs in the middle · humans for consequential decisions</Text>
    </MantineShell>
  );
}
