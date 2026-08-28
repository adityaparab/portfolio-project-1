/** Exception Review — detail workspace (issue #32): 3-way comparison,
 * extracted fields with confidences, triage recommendation, decision form
 * with the four-eyes confirmation step. */
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Table,
  Text,
  Textarea,
  TextInput,
  Title,
} from "@mantine/core";
import { useMemo, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate, useParams } from "react-router-dom";
import { useDecision } from "~/hooks/useDecision";
import { useInvoiceDetail } from "~/hooks/useExceptionQueue";
import { DecisionRequestSchema } from "~/api/schemas";
import type { DecisionRequest } from "~/api/schemas";
import { formatConfidence, formatMoney } from "~/lib/format";
import { DecisionFormConfirm } from "~/components/DecisionFormConfirm";
import classes from "./ExceptionDetailPage.module.css";

interface MatchFinding {
  code: string;
  severity: string;
  detail: string;
  line_no: string | null;
  delta: Record<string, string> | null;
}

export function ExceptionDetailPage() {
  const { invoiceId } = useParams();
  const navigate = useNavigate();
  const id = invoiceId ? Number(invoiceId) : null;
  const detail = useInvoiceDetail(id);

  const exceptionView = detail.data?.exception as
    | { exception_id: number; type: string; status: string; recommendation: RecPayload | null }
    | undefined;
  const exceptionId =
    exceptionView && "exception_id" in exceptionView ? exceptionView.exception_id : null;

  const decision = useDecision(exceptionId);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const form = useForm<DecisionRequest>({
    resolver: zodResolver(DecisionRequestSchema),
    defaultValues: { action: "APPROVE", rationale: "", reason_code: "", escalate_to: null },
  });
  const action = form.watch("action");

  const findings: MatchFinding[] = useMemo(() => {
    const match = detail.data?.match as { findings?: MatchFinding[] } | null | undefined;
    return match?.findings ?? [];
  }, [detail.data]);

  if (detail.isLoading) return <Loader className={classes.loader} />;
  if (detail.isError || !detail.data) {
    return (
      <Alert className={classes.alert} title="Could not load invoice">
        {String(detail.error)}
      </Alert>
    );
  }

  const { invoice, extraction } = detail.data;
  const policyFindings = detail.data.policy as unknown as MatchFinding[];
  const recommendation = exceptionView?.recommendation ?? null;

  const onSubmit = form.handleSubmit(() => setConfirmOpen(true));
  const confirmSubmit = async () => {
    setConfirmOpen(false);
    try {
      await decision.mutateAsync(form.getValues());
      navigate("/queue"); // conflicts stay put — the toast explains why
    } catch {
      // error surface (four-eyes / already-decided) handled by the hook
    }
  };

  return (
    <div className={classes.page}>
      <div className={classes.header}>
        <Title order={2}>{invoice.invoice_number ?? `Invoice #${invoice.invoice_id}`}</Title>
        <Group className={classes.headerBadges}>
          <Badge variant="light" className={classes.badgeException}>
            {exceptionView?.type.replace(/_/g, " ") ?? invoice.status}
          </Badge>
          {invoice.run?.route && <Badge variant="outline">{invoice.run.route}</Badge>}
        </Group>
      </div>

      <div className={classes.grid}>
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            3-way match — deltas
          </Title>
          {findings.length === 0 && policyFindings.length === 0 && (
            <Text className={classes.muted}>No deterministic findings recorded.</Text>
          )}
          {findings.map((finding, i) => (
            <div key={i} className={classes.finding}>
              <Badge variant="light" className={classes.badgeHigh}>
                {finding.code.replace(/_/g, " ")}
              </Badge>
              <Text className={classes.findingDetail}>{finding.detail}</Text>
              {finding.delta && (
                <code className={classes.delta}>
                  {Object.entries(finding.delta)
                    .map(([k, v]) => `${k}=${v}`)
                    .join("  ")}
                </code>
              )}
            </div>
          ))}
          {policyFindings.map((finding, i) => (
            <div key={`p${i}`} className={classes.finding}>
              <Badge variant="light" className={classes.badgePolicy}>
                {finding.code.replace(/_/g, " ")}
              </Badge>
              <Text className={classes.findingDetail}>{finding.detail}</Text>
            </div>
          ))}
        </Card>

        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Extracted fields — confidence per field
          </Title>
          <Table>
            <Table.Tbody>
              {fieldRows(extraction, detail.data.lines).map((row) => (
                <Table.Tr key={row.label}>
                  <Table.Td className={classes.fieldLabel}>{row.label}</Table.Td>
                  <Table.Td className={classes.mono}>{row.value}</Table.Td>
                  <Table.Td>
                    <Badge
                      variant="light"
                      className={
                        (row.confidence ?? 0) >= 0.9 ? classes.badgeOk : classes.badgeWarn
                      }
                    >
                      {formatConfidence(row.confidence)}
                    </Badge>
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>

        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Triage agent
          </Title>
          {recommendation ? (
            <div className={classes.recommendation}>
              <Group className={classes.recHeader}>
                <Badge variant="light" className={classes.badgeAgent}>
                  {recommendation.classification.replace(/_/g, " ")}
                </Badge>
                <Badge variant="outline">
                  conf {formatConfidence(recommendation.confidence)}
                </Badge>
                {recommendation.abstained && (
                  <Badge variant="light" className={classes.badgeWarn}>
                    abstained — needs human judgment
                  </Badge>
                )}
              </Group>
              <Text className={classes.recText}>{recommendation.recommendation}</Text>
              <Text className={classes.recRationale}>{recommendation.rationale}</Text>
            </div>
          ) : (
            <Text className={classes.muted}>
              No agent recommendation (degraded or not run) — deterministic findings above are
              complete.
            </Text>
          )}
        </Card>

        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Decision
          </Title>
          <form onSubmit={onSubmit} className={classes.form}>
            <Controller
              name="action"
              control={form.control}
              render={({ field }) => (
                <Group className={classes.actions} data-testid="decision-actions">
                  {(
                    [
                      ["APPROVE", "Approve"],
                      ["RETURN_TO_VENDOR", "Return to vendor"],
                      ["ESCALATE", "Escalate"],
                    ] as const
                  ).map(([value, label]) => (
                    <Button
                      key={value}
                      variant={field.value === value ? "filled" : "light"}
                      onClick={() => field.onChange(value)}
                      className={field.value === value ? classes.actionActive : classes.action}
                      type="button"
                    >
                      {label}
                    </Button>
                  ))}
                </Group>
              )}
            />
            {action === "ESCALATE" && (
              <TextInput
                label="Escalate to"
                placeholder="director-queue"
                {...form.register("escalate_to")}
              />
            )}
            <Textarea
              label="Rationale (audited)"
              placeholder="Why this decision — cited in the ledger and provenance export"
              autosize
              minRows={3}
              error={form.formState.errors.rationale?.message}
              {...form.register("rationale")}
            />
            <TextInput
              label="Reason code"
              placeholder="e.g. PRICE_TOLERATED"
              error={form.formState.errors.reason_code?.message}
              {...form.register("reason_code")}
            />
            <Button type="submit" className={classes.submit} data-testid="decision-submit">
              Review decision
            </Button>
          </form>
        </Card>
      </div>

      <DecisionFormConfirm
        opened={confirmOpen}
        action={action}
        rationale={form.watch("rationale")}
        fourEyes={action === "APPROVE"}
        submitting={decision.isPending}
        onConfirm={confirmSubmit}
        onCancel={() => setConfirmOpen(false)}
      />
    </div>
  );
}

interface RecPayload {
  classification: string;
  confidence: number;
  abstained: boolean;
  recommendation: string;
  rationale: string;
}

function fieldRows(
  extraction: { vendor_name: string | null; invoice_number: string | null; po_number: string | null; currency: string | null; total_amount: string | null; tax_total: string | null; min_confidence: number | null; confidences: Record<string, number> } | null,
  lines: { line_no: string; qty: string | null; unit_price: string | null; line_total: string | null }[],
): { label: string; value: string; confidence: number | null }[] {
  if (!extraction) return [];
  const conf = (key: string) => extraction.confidences[key] ?? null;
  const rows = [
    { label: "Vendor", value: extraction.vendor_name ?? "—", confidence: conf("vendor_name") },
    { label: "Invoice #", value: extraction.invoice_number ?? "—", confidence: conf("invoice_number") },
    { label: "PO ref", value: extraction.po_number ?? "—", confidence: conf("po_number") },
    { label: "Currency", value: extraction.currency ?? "—", confidence: conf("currency") },
    { label: "Total", value: formatMoney(extraction.total_amount), confidence: conf("total_amount") },
    { label: "Tax", value: formatMoney(extraction.tax_total), confidence: conf("tax_total") },
  ];
  for (const line of lines.slice(0, 6)) {
    rows.push({
      label: `Line ${line.line_no}`,
      value: `${line.qty} × ${formatMoney(line.unit_price)} = ${formatMoney(line.line_total)}`,
      confidence: extraction.confidences[`line[${line.line_no}].unit_price`] ?? null,
    });
  }
  return rows;
}
