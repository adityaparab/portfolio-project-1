/** Agent Run (issue #36): live node-by-node graph progress from the trace
 * endpoint — node pipeline + ledger timeline with actor badges, polled at a
 * single interval until the run settles. */
import { Badge, Card, Group, NumberInput, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useRunTrace } from "~/hooks/useRunTrace";
import classes from "./AgentRunPage.module.css";

const NODES = [
  ["ingest", "Ingest"],
  ["extract", "Extract"],
  ["validate", "Validate"],
  ["match3way", "Match 3-way"],
  ["policy", "Policy"],
  ["gate", "Gate"],
  ["exception_triage", "Exception triage"],
  ["human_review", "Human review"],
  ["auto_approve", "Auto-approve"],
  ["archive", "Archive"],
  ["reject", "Reject"],
] as const;

export function AgentRunPage() {
  const [runIdInput, setRunIdInput] = useState<number | null>(null);
  const trace = useRunTrace(runIdInput);
  const data = trace.data;
  const reached = new Set(data?.node_trace ?? []);

  return (
    <div className={classes.page}>
      <Title order={2}>Agent Run</Title>
      <Group className={classes.controls}>
        <NumberInput
          placeholder="Run id"
          value={runIdInput ?? ""}
          onChange={(value) => setRunIdInput(typeof value === "number" ? value : null)}
          className={classes.runInput}
          data-testid="run-id-input"
        />
        {data && (
          <Group className={classes.meta}>
            <Badge variant="outline">run #{data.run_id}</Badge>
            <Badge variant="light" className={statusClass(data.status)}>
              {data.status}
            </Badge>
            {data.route && <Badge variant="light">{data.route}</Badge>}
            {data.confidence != null && (
              <Badge variant="outline">conf {data.confidence.toFixed(3)}</Badge>
            )}
          </Group>
        )}
      </Group>

      <Card className={classes.card} withBorder>
        <Title order={4} className={classes.cardTitle}>
          Graph progress
        </Title>
        {trace.isError && <Text className={classes.error}>{String(trace.error)}</Text>}
        {!trace.isError && !data && (
          <Text className={classes.muted}>Enter a run id to watch its pipeline.</Text>
        )}
        {data && (
          <div className={classes.pipeline}>
            {NODES.map(([node, label]) => (
              <div key={node} className={classes.nodeCell}>
                <span
                  className={reached.has(node) ? classes.nodeDone : classes.nodePending}
                  data-testid={`node-${node}`}
                >
                  {label}
                </span>
              </div>
            ))}
          </div>
        )}
      </Card>

      {data && (
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Ledger timeline
          </Title>
          <div className={classes.timeline}>
            {data.timeline.map((entry) => (
              <div key={entry.seq} className={classes.timelineRow}>
                <span className={classes.seq}>#{entry.seq}</span>
                <Badge variant="light" className={actorClass(entry.actor_type)}>
                  {entry.actor_type}
                </Badge>
                <span className={classes.eventName}>{entry.event}</span>
                <span className={classes.timestamp}>{entry.created_at.slice(11, 19)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function statusClass(status: string): string {
  switch (status) {
    case "COMPLETED":
      return classes.badgeOk;
    case "FAILED":
      return classes.badgeDown;
    case "AWAITING_DECISION":
      return classes.badgeWarn;
    default:
      return classes.badgeRun;
  }
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
