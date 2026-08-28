/** Audit & Trace (issue #37, Priya's view): Mantine Timeline over the run,
 * full ledger with version pins, and the provenance export for one invoice.
 * The persona RBAC gates the data: analysts get a clear role notice. */
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  NumberInput,
  Text,
  Timeline,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { usePersona } from "~/personas/PersonaContext";
import { useProvenance } from "~/hooks/useProvenance";
import { useRunTrace } from "~/hooks/useRunTrace";
import classes from "./AuditPage.module.css";

export function AuditPage() {
  const { persona } = usePersona();
  const [invoiceId, setInvoiceId] = useState<number | null>(null);
  const provenance = useProvenance(invoiceId);
  const data = provenance.data;
  const runId = data?.runs.at(-1)?.run_id ?? null;
  const trace = useRunTrace(runId); // timeline view of the latest run

  return (
    <div className={classes.page}>
      <Title order={2}>Audit &amp; Trace</Title>

      <Group className={classes.controls}>
        <NumberInput
          placeholder="Invoice id"
          value={invoiceId ?? ""}
          onChange={(value) => setInvoiceId(typeof value === "number" ? value : null)}
          className={classes.input}
          data-testid="audit-invoice-id"
        />
        <Text className={classes.personaNote} component="span">
          viewing as {persona.name} ({persona.role}) — provenance needs audit/platform
        </Text>
      </Group>

      {provenance.isError && (
        <Alert className={classes.alert} title="Provenance unavailable">
          {String(provenance.error)}
        </Alert>
      )}

      {data && (
        <>
          <div className={classes.grid}>
            <Card className={classes.card} withBorder>
              <Title order={4} className={classes.cardTitle}>
                Runs
              </Title>
              {data.runs.map((run) => (
                <div key={run.run_id} className={classes.runRow}>
                  <Badge variant="outline">run #{run.run_id}</Badge>
                  <Badge variant="light">{run.route ?? "—"}</Badge>
                  <Text className={classes.mono}>graph {run.graph_version}</Text>
                  {run.confidence != null && (
                    <Text className={classes.mono}>conf {run.confidence.toFixed(3)}</Text>
                  )}
                </div>
              ))}

              <Title order={4} className={classes.cardTitle}>
                Decisions
              </Title>
              {data.decisions.length === 0 && (
                <Text className={classes.muted}>No human decisions recorded.</Text>
              )}
              {data.decisions.map((decision) => (
                <div key={decision.decision_id} className={classes.decisionRow}>
                  <Badge variant="light" className={classes.badgeHuman}>
                    {decision.actor_user}
                  </Badge>
                  <Text className={classes.mono}>{decision.action}</Text>
                  <Text className={classes.muted}>{decision.reason_code}</Text>
                  <Text className={classes.rationale}>{decision.rationale}</Text>
                </div>
              ))}

              <Title order={4} className={classes.cardTitle}>
                Exceptions
              </Title>
              {data.exceptions.map((exception) => (
                <div key={exception.exception_id} className={classes.runRow}>
                  <Badge variant="light" className={classes.badgeException}>
                    {exception.type}
                  </Badge>
                  <Text className={classes.mono}>{exception.severity}</Text>
                  <Text className={classes.muted}>{exception.status}</Text>
                </div>
              ))}
            </Card>

            <Card className={classes.card} withBorder>
              <Title order={4} className={classes.cardTitle}>
                Run timeline
              </Title>
              {trace.data ? (
                <Timeline className={classes.timeline} bulletSize={18} lineWidth={2}>
                  {trace.data.timeline.map((entry) => (
                    <Timeline.Item
                      key={entry.seq}
                      title={
                        <span className={classes.eventName}>
                          #{entry.seq} {entry.event}
                        </span>
                      }
                    >
                      <Group className={classes.entryMeta}>
                        <Badge variant="light" className={actorClass(entry.actor_type)}>
                          {entry.actor_type}
                        </Badge>
                        <Text className={classes.timestamp}>
                          {entry.created_at.slice(11, 19)}
                        </Text>
                      </Group>
                    </Timeline.Item>
                  ))}
                </Timeline>
              ) : (
                <Text className={classes.muted}>No trace available.</Text>
              )}
            </Card>
          </div>

          <Card className={classes.card} withBorder>
            <Group className={classes.exportHeader}>
              <Title order={4}>Full ledger ({data.ledger.length} entries)</Title>
              <Button
                variant="light"
                className={classes.exportButton}
                onClick={() => downloadProvenance(data)}
                data-testid="export-provenance"
              >
                Export JSON
              </Button>
            </Group>
            <div className={classes.ledger}>
              {data.ledger.map((entry) => (
                <div key={`${entry.seq}-${entry.event}`} className={classes.ledgerRow}>
                  <span className={classes.seq}>#{entry.seq}</span>
                  <Badge variant="light" className={actorClass(entry.actor_type)}>
                    {entry.actor_type}
                  </Badge>
                  <span className={classes.eventName}>{entry.event}</span>
                  <span className={classes.pins}>
                    {pinsOf(entry)}
                  </span>
                  <span className={classes.timestamp}>{entry.created_at.slice(11, 19)}</span>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function pinsOf(entry: {
  versions: Record<string, unknown> | null;
  policy_version: string | null;
  prompt_template_version: string | null;
}): string {
  const parts: string[] = [];
  if (entry.versions && "graph" in entry.versions) parts.push(`graph=${entry.versions.graph}`);
  if (entry.policy_version) parts.push(`policy=${entry.policy_version}`);
  if (entry.prompt_template_version) parts.push(`prompt=${entry.prompt_template_version}`);
  return parts.join(" · ");
}

function downloadProvenance(data: unknown): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "provenance-export.json";
  anchor.click();
  URL.revokeObjectURL(url);
}

function actorClass(actor: string): string {
  switch (actor) {
    case "AGENT":
      return classes.badgeAgent;
    case "HUMAN":
      return classes.badgeHuman;
    case "POLICY":
      return classes.badgePolicy;
    default:
      return classes.badgeSystem;
  }
}
