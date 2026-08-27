/**
 System Status — the scaffold's proof-of-life page (#31 AC): the generated
 client round-trips against the real API and the persona identity headers
 are observably attached.
*/
import { Badge, Card, Group, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { usePersona } from "~/personas/PersonaContext";
import { api } from "~/api/client";
import { HealthSchema, ReadySchema } from "~/api/schemas";
import type { Ready } from "~/api/schemas";
import classes from "./StatusPage.module.css";

async function fetchReady(): Promise<Ready> {
  const { data, response } = await api.GET("/readyz");
  if (!response.ok || !data) throw new Error(`readyz HTTP ${response.status}`);
  return ReadySchema.parse(data);
}

export function StatusPage() {
  const { persona, headers } = usePersona();
  const health = useQuery({
    queryKey: ["system", "health"],
    queryFn: async () => {
      const { data, response } = await api.GET("/healthz");
      if (!response.ok || !data) throw new Error(`healthz HTTP ${response.status}`);
      return HealthSchema.parse(data);
    },
  });
  const ready = useQuery({ queryKey: ["system", "ready"], queryFn: fetchReady });

  return (
    <div className={classes.page}>
      <Title order={2}>System status</Title>
      <Card className={classes.card} withBorder>
        <Group className={classes.row}>
          <Text>API (generated client round-trip)</Text>
          <Badge
            variant="light"
            className={
              health.isError
                ? classes.badgeDown
                : health.data?.status === "ok"
                  ? classes.badgeOk
                  : classes.badgeWarn
            }
          >
            {health.isError ? "unreachable" : (health.data?.status ?? "…")}
          </Badge>
        </Group>
        {ready.data && (
          <div className={classes.checks}>
            {ready.data.checks.map((check) => (
              <Group key={check.name} className={classes.row}>
                <Text className={classes.checkName}>{check.name}</Text>
                <Badge variant="light" className={check.ok ? classes.badgeOk : classes.badgeDown}>
                  {check.ok ? "ok" : (check.detail ?? "down")}
                </Badge>
              </Group>
            ))}
          </div>
        )}
      </Card>
      <Card className={classes.card} withBorder>
        <Text className={classes.cardTitle}>Active persona (headers on every request)</Text>
        <Group className={classes.row}>
          <Text>{persona.name}</Text>
          <code className={classes.headers} data-testid="identity-headers">
            {Object.entries(headers)
              .map(([k, v]) => `${k}: ${v}`)
              .join("  ·  ")}
          </code>
        </Group>
      </Card>
    </div>
  );
}
