/** Agent Run (issue #36 + model-call audit): clickable step view — each
 * pipeline stage lights up as it completes, the ACTIVE stage blinks green,
 * and clicking a step shows its ledger payload plus the recorded LLM call
 * (model, prompt version, reasoning, output) or its pending/running state. */
import { Badge, Card, Code, Group, NumberInput, Text, Title } from "@mantine/core";
import { useState } from "react";
import { useModelCalls, useRunTrace } from "~/hooks/useRunTrace";
import type { ModelCall, RunTrace } from "~/hooks/useRunTrace";
import classes from "./AgentRunPage.module.css";

interface StepDef {
  node: string;
  label: string;
  event: string;
  llm: string | null; // stage key in model_calls / stage_models
}

const STEPS: StepDef[] = [
  { node: "ingest", label: "Ingest", event: "run.started", llm: null },
  { node: "extract", label: "Extract", event: "extract.completed", llm: "extract" },
  { node: "validate", label: "Validate", event: "validate.completed", llm: null },
  { node: "match3way", label: "Match 3-way", event: "match.completed", llm: null },
  { node: "policy", label: "Policy", event: "policy.evaluated", llm: "policy" },
  { node: "gate", label: "Gate", event: "gate.decided", llm: null },
  { node: "exception_triage", label: "Exception triage", event: "triage.completed", llm: "triage" },
  { node: "auto_approve", label: "Auto-approve", event: "invoice.auto_approved", llm: null },
  { node: "human_review", label: "Human review", event: "human_review.completed", llm: null },
  { node: "archive", label: "Archive", event: "run.archived", llm: null },
  { node: "reject", label: "Reject", event: "run.rejected", llm: null },
];

type StepState = "done" | "active" | "pending";

export function AgentRunPage() {
  const [runIdInput, setRunIdInput] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const trace = useRunTrace(runIdInput);
  const data = trace.data;
  const settled = Boolean(
    data && (data.status === "COMPLETED" || data.status === "REJECTED" || data.status === "FAILED"),
  );
  const modelCalls = useModelCalls(runIdInput, settled);
  const reached = new Set(data?.node_trace ?? []);
  const activeNode = data?.active_node ?? null;

  const stepState = (node: string): StepState => {
    if (reached.has(node)) return "done";
    if (node === activeNode) return "active";
    return "pending";
  };

  // only show steps reachable on this run's path once we know where it's going
  const visibleSteps = STEPS.filter(
    (step) =>
      stepState(step.node) !== "pending" ||
      step.node === activeNode ||
      step.node === "reject" ||
      step.node === "archive" ||
      step.node === "human_review" ||
      (data?.route === "EXCEPTION" && step.node === "exception_triage") ||
      reached.size < 3, // early run: show the standard chain
  );

  return (
    <div className={classes.page}>
      <Title order={2}>Agent Run</Title>
      <Group className={classes.controls}>
        <NumberInput
          placeholder="Run id"
          value={runIdInput ?? ""}
          onChange={(value) => {
            setRunIdInput(typeof value === "number" ? value : null);
            setSelected(null);
          }}
          className={classes.runInput}
          data-testid="run-id-input"
        />
        {data && (
          <Group className={classes.meta}>
            <Badge variant="outline">run #{data.run_id}</Badge>
            <Badge variant="light" className={statusClass(data.status, classes)}>
              {data.status}
            </Badge>
            {data.route && <Badge variant="light">{data.route}</Badge>}
            {data.confidence != null && (
              <Badge variant="outline">conf {data.confidence.toFixed(3)}</Badge>
            )}
            {activeNode && (
              <Badge variant="light" className={classes.badgeLive} data-testid="active-badge">
                <span className={classes.blinkDot} />
                {activeNode}
              </Badge>
            )}
          </Group>
        )}
      </Group>

      {trace.isError && <Text className={classes.error}>{String(trace.error)}</Text>}
      {!trace.isError && !data && (
        <Text className={classes.muted}>Enter a run id to watch its pipeline.</Text>
      )}

      {data && (
        <>
          <Card className={classes.card} withBorder>
            <Title order={4} className={classes.cardTitle}>
              Pipeline steps — click for details
            </Title>
            <div className={classes.steps} role="list">
              {visibleSteps.map((step) => {
                const state = stepState(step.node);
                const calls = modelCallsFor(modelCalls, step.llm);
                return (
                  <button
                    key={step.node}
                    type="button"
                    className={stepClass(state, selected === step.node, classes)}
                    onClick={() => setSelected(step.node)}
                    data-testid={`step-${step.node}`}
                  >
                    <span className={classes.stepLabel}>
                      {state === "active" && <span className={classes.blinkDot} />}
                      {step.label}
                    </span>
                    <span className={classes.stepState}>
                      {state === "done" && (calls.length > 0 ? "✓ LLM" : "✓")}
                      {state === "active" && "running"}
                      {state === "pending" && "—"}
                    </span>
                  </button>
                );
              })}
            </div>
          </Card>

          {selected && (
            <StepDetail
              step={STEPS.find((s) => s.node === selected)!}
              state={stepState(selected)}
              trace={data}
              calls={modelCallsFor(modelCalls, STEPS.find((s) => s.node === selected)?.llm ?? null)}
            />
          )}
        </>
      )}
    </div>
  );
}

function StepDetail({
  step,
  state,
  trace,
  calls,
}: {
  step: StepDef;
  state: StepState;
  trace: RunTrace;
  calls: ModelCall[];
}) {
  const event = [...trace.timeline].reverse().find((e) => e.event === step.event);
  const stageInfo = (trace.stage_models as Record<string, unknown> | undefined)?.[step.llm ?? ""];
  const model = stageInfo as { alias?: string; wire_model?: string } | undefined;

  return (
    <Card className={classes.card} withBorder data-testid={`detail-${step.node}`}>
      <Group className={classes.detailHeader}>
        <Title order={4}>{step.label}</Title>
        <Badge variant="light" className={stateClass(state === "done" ? "done" : state, classes)}>
          {state === "done" ? "completed" : state}
        </Badge>
        {state === "active" && step.llm && model?.wire_model && (
          <Badge variant="light" className={classes.badgeLive}>
            <span className={classes.blinkDot} />
            {model.wire_model}
          </Badge>
        )}
      </Group>

      {state === "pending" && (
        <Text className={classes.muted}>
          Not started on this run{step.llm ? " — no model output yet" : ""}.
        </Text>
      )}

      {state === "active" && (
        <Text className={classes.running}>
          Running now{step.llm && model?.wire_model ? ` on ${model.wire_model}` : ""} — model
          output appears here the moment the call completes.
        </Text>
      )}

      {calls.map((call) => (
        <div key={call.call_id} className={classes.modelCall}>
          <Group className={classes.callMeta}>
            <Badge variant="light" className={classes.badgeModel}>
              {call.wire_model}
            </Badge>
            <Text className={classes.mono}>{call.alias}</Text>
            {call.prompt_version && (
              <Text className={classes.mono}>{call.prompt_version}</Text>
            )}
            {call.latency_ms != null && (
              <Text className={classes.mono}>{call.latency_ms} ms</Text>
            )}
          </Group>
          {call.reasoning_text && (
            <details className={classes.reasoning}>
              <summary className={classes.reasoningSummary}>Reasoning / thinking</summary>
              <Code className={classes.callText} block>
                {call.reasoning_text}
              </Code>
            </details>
          )}
          <Text className={classes.outputLabel}>Output</Text>
          <Code className={classes.callText} block>
            {call.output_text}
          </Code>
        </div>
      ))}

      {event && (
        <div className={classes.ledgerBlock}>
          <Text className={classes.outputLabel}>Ledger — {event.event}</Text>
          <Code className={classes.callText} block>
            {JSON.stringify(event.payload, null, 2)}
          </Code>
        </div>
      )}
    </Card>
  );
}

function modelCallsFor(calls: ModelCall[] | undefined, stage: string | null): ModelCall[] {
  if (!calls || stage === null) return [];
  return calls.filter((call) => call.stage === stage);
}

function stateClass(state: string, c: typeof classes): string {
  switch (state) {
    case 'done':
    case 'COMPLETED':
      return c.badgeOk;
    case 'active':
    case 'RUNNING':
      return c.badgeRun;
    case 'AWAITING_DECISION':
      return c.badgeWarn;
    case 'FAILED':
      return c.badgeDown;
    default:
      return c.badgeRun;
  }
}

function statusClass(status: string, c: typeof classes): string {
  switch (status) {
    case "COMPLETED":
      return c.badgeOk;
    case "FAILED":
      return c.badgeDown;
    case "AWAITING_DECISION":
      return c.badgeWarn;
    default:
      return c.badgeRun;
  }
}

function stepClass(state: StepState, selected: boolean, c: typeof classes): string {
  const base = selected ? c.stepSelected : c.step;
  const stateClass =
    state === "done" ? c.stepDone : state === "active" ? c.stepActive : c.stepPending;
  return `${base} ${stateClass}`;
}
