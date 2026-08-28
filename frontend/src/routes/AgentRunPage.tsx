/** Agent Run — master/detail: vertical Mantine Stepper (left) + step detail
 * (right). The Stepper is CONTROLLED: progress derives from the run's
 * checkpoint state (never from clicks); clicking a step only selects it for
 * inspection. The active stage pulses green and names its wire model; LLM
 * steps show the recorded reasoning + output from the model_calls audit
 * trail, non-LLM steps show their ledger payload; pending steps show status. */
import { Badge, Card, Code, Group, NumberInput, Stepper, Text, Title } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { useModelCalls, useRunTrace } from "~/hooks/useRunTrace";
import type { ModelCall, RunTrace } from "~/hooks/useRunTrace";
import classes from "./AgentRunPage.module.css";

interface StepDef {
  node: string;
  label: string;
  event: string;
  llm: string | null; // stage key in model_calls / stage_models
}

const ALL_STEPS: StepDef[] = [
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
  // Deep-linkable: /runs/:runId seeds the input (intake navigates here on
  // upload); manual typing still works on the bare /runs route.
  const { runId } = useParams();
  const [runIdInput, setRunIdInput] = useState<number | null>(
    runId ? Number(runId) : null,
  );
  const [selected, setSelected] = useState<string | null>(null);
  useEffect(() => {
    setRunIdInput(runId ? Number(runId) : null);
    setSelected(null);
  }, [runId]);
  const trace = useRunTrace(runIdInput);
  const data = trace.data;
  const settled = Boolean(
    data && (data.status === "COMPLETED" || data.status === "REJECTED" || data.status === "FAILED"),
  );
  const modelCalls = useModelCalls(runIdInput, settled);
  const reached = useMemo(() => new Set(data?.node_trace ?? []), [data]);
  const activeNode = data?.active_node ?? null;
  const running = data?.status === "RUNNING";

  const stepState = (node: string): StepState => {
    if (reached.has(node)) return "done";
    if (node === activeNode) return "active";
    return "pending";
  };

  // Steps on this run's path, in canonical order — Mantine renders every
  // step before `active` as completed, so the list must contain only steps
  // this run actually executes (the branch resolves once the trace shows it).
  const steps = useMemo<StepDef[]>(() => {
    const hit = (node: string) => reached.has(node) || activeNode === node;
    if (hit("reject")) return ALL_STEPS.filter((s) => s.node === "ingest" || s.node === "reject");
    const prefix = ["ingest", "extract", "validate", "match3way", "policy", "gate"].filter(hit);
    let tail: string[];
    if (hit("exception_triage") || data?.route === "EXCEPTION") {
      tail = ["exception_triage", "human_review", "archive"];
    } else if (hit("auto_approve") || data?.route === "AUTO") {
      tail = ["auto_approve", "archive"];
    } else {
      // pre-branch: both continuations stay pending (nothing after the
      // branch point is completed yet, so Mantine's rendering stays true)
      tail = ["exception_triage", "auto_approve", "human_review", "archive"];
    }
    const keep = new Set([...prefix, ...tail]);
    return ALL_STEPS.filter((s) => keep.has(s.node));
  }, [reached, activeNode, data?.route]);

  // Controlled progress: the executing node while RUNNING; otherwise the
  // last completed step on the path.
  const progressIndex = useMemo(() => {
    if (steps.length === 0) return 0;
    if (running && activeNode) {
      const i = steps.findIndex((s) => s.node === activeNode);
      if (i >= 0) return i;
    }
    let last = 0;
    steps.forEach((s, i) => {
      if (reached.has(s.node)) last = i;
    });
    return last;
  }, [steps, running, activeNode, reached]);

  const stageModels = (data?.stage_models ?? {}) as Record<
    string,
    { alias?: string; wire_model?: string }
  >;
  const selectedStep = selected ? ALL_STEPS.find((s) => s.node === selected) : null;

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
            <Badge variant="light" className={stateClass(data.status)}>
              {data.status}
            </Badge>
            {data.route && <Badge variant="light">{data.route}</Badge>}
            {data.confidence != null && (
              <Badge variant="outline">conf {data.confidence.toFixed(3)}</Badge>
            )}
            {running && activeNode && (
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
        <div className={classes.layout}>
          <Card className={classes.masterCard} withBorder>
            <Title order={4} className={classes.cardTitle}>
              Pipeline
            </Title>
            <Stepper
              active={progressIndex}
              onStepClick={(index) => setSelected(steps[index]?.node ?? null)}
              orientation="vertical"
              size="sm"
              classNames={{
                root: classes.stepper,
                step: classes.step,
                stepIcon: classes.stepIcon,
                stepLabel: classes.stepLabel,
                separator: classes.separator,
              }}
            >
              {steps.map((step) => {
                const state = stepState(step.node);
                const model = step.llm ? stageModels[step.llm] : undefined;
                return (
                  <Stepper.Step
                    key={step.node}
                    loading={state === "active"}
                    label={
                      <span className={classes.stepLabelText}>
                        {state === "active" && <span className={classes.blinkDot} />}
                        {step.label}
                      </span>
                    }
                    description={
                      state === "active"
                        ? `running${model?.wire_model ? ` · ${model.wire_model}` : ""}`
                        : (model?.wire_model ?? undefined)
                    }
                    data-testid={`step-${step.node}`}
                  />
                );
              })}
            </Stepper>
          </Card>

          <Card className={classes.detailCard} withBorder>
            {selectedStep ? (
              <StepDetail
                step={selectedStep}
                state={stepState(selectedStep.node)}
                trace={data}
                calls={(modelCalls ?? []).filter((c) => c.stage === selectedStep.llm)}
              />
            ) : (
              <Text className={classes.muted}>Select a step to inspect its output.</Text>
            )}
          </Card>
        </div>
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
  const model = step.llm
    ? ((trace.stage_models as Record<string, unknown>)[step.llm] as
        | { alias?: string; wire_model?: string }
        | undefined)
    : undefined;

  return (
    <div data-testid={`detail-${step.node}`}>
      <Group className={classes.detailHeader}>
        <Title order={4}>{step.label}</Title>
        <Badge variant="light" className={stateClass(state)}>
          {state === "done" ? "completed" : state}
        </Badge>
        {state === "active" && model?.wire_model && (
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
            {call.prompt_version && <Text className={classes.mono}>{call.prompt_version}</Text>}
            {call.latency_ms != null && <Text className={classes.mono}>{call.latency_ms} ms</Text>}
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
    </div>
  );
}

function stateClass(state: string): string {
  switch (state) {
    case "done":
    case "COMPLETED":
      return classes.badgeOk;
    case "active":
    case "RUNNING":
      return classes.badgeRun;
    case "AWAITING_DECISION":
      return classes.badgeWarn;
    case "FAILED":
      return classes.badgeDown;
    default:
      return classes.badgeRun;
  }
}
