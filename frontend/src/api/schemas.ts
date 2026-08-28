/**
 Zod twins of the Pydantic API contracts (api/schemas/*). Rule: when a
 backend model changes, its twin changes in the same commit. Responses are
 parsed at the boundary — the wire is never trusted (AGENTS.md).
*/
import { z } from "zod";

export const HealthSchema = z.object({ status: z.string() });

export const ReadyCheckSchema = z.object({
  name: z.string(),
  ok: z.boolean(),
  detail: z.string().nullable(),
});

export const ReadySchema = z.object({
  status: z.string(),
  checks: z.array(ReadyCheckSchema),
});

export const QueueExceptionSummarySchema = z.object({
  exception_id: z.number().int(),
  type: z.string(),
  severity: z.string(),
  status: z.string(),
  assignee: z.string().nullable(),
  sla_due_at: z.string().nullable(),
  sla_overdue_seconds: z.number().int().nullable(),
});

export const QueueRunSummarySchema = z.object({
  run_id: z.number().int(),
  route: z.string().nullable(),
  status: z.string(),
  confidence: z.number().nullable(),
});

export const QueueItemSchema = z.object({
  invoice_id: z.number().int(),
  invoice_number: z.string().nullable(),
  status: z.string(),
  vendor_id: z.number().int().nullable(),
  currency: z.string().nullable(),
  amount_total: z.string().nullable(),
  issue_date: z.string().nullable(),
  created_at: z.string(),
  run: QueueRunSummarySchema.nullable(),
  exception: QueueExceptionSummarySchema.nullable(),
});

export const QueuePageSchema = z.object({
  items: z.array(QueueItemSchema),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
});

export type Ready = z.infer<typeof ReadySchema>;

export type QueuePage = z.infer<typeof QueuePageSchema>;
export type QueueItem = z.infer<typeof QueueItemSchema>;

export const ExtractionLineSchema = z.object({
  line_no: z.string(),
  description: z.string().nullable(),
  qty: z.string().nullable(),
  uom: z.string().nullable(),
  unit_price: z.string().nullable(),
  tax_code: z.string().nullable(),
  line_total: z.string().nullable(),
});

export const ExtractionSchema = z.object({
  vendor_name: z.string().nullable(),
  invoice_number: z.string().nullable(),
  po_number: z.string().nullable(),
  issue_date: z.string().nullable(),
  due_date: z.string().nullable(),
  currency: z.string().nullable(),
  total_amount: z.string().nullable(),
  tax_total: z.string().nullable(),
  iban: z.string().nullable(),
  lines: z.array(ExtractionLineSchema),
  confidences: z.record(z.string(), z.number()),
  min_confidence: z.number().nullable(),
});

export const FindingSchema = z.record(z.string(), z.unknown());

export const LedgerEntrySchema = z.object({
  seq: z.number().int(),
  actor_type: z.string(),
  actor_id: z.string().nullable(),
  event: z.string(),
  created_at: z.string(),
  versions: z.record(z.string(), z.unknown()).nullable(),
  policy_version: z.string().nullable(),
  prompt_template_version: z.string().nullable(),
});

export const InvoiceDetailSchema = z.object({
  invoice: QueueItemSchema,
  lines: z.array(ExtractionLineSchema),
  extraction: ExtractionSchema.nullable(),
  validation: z.array(FindingSchema),
  match: z.record(z.string(), z.unknown()).nullable(),
  policy: z.array(FindingSchema),
  gate: z.record(z.string(), z.unknown()).nullable(),
  exception: z.record(z.string(), z.unknown()).nullable(),
  ledger: z.object({
    entry_count: z.number().int(),
    last_entries: z.array(LedgerEntrySchema),
  }),
  state_available: z.boolean(),
});

export type InvoiceDetail = z.infer<typeof InvoiceDetailSchema>;

export const DecisionRequestSchema = z.object({
  action: z.enum(["APPROVE", "RETURN_TO_VENDOR", "ESCALATE"]),
  rationale: z.string().min(10, "Rationale is required for the audit trail (10+ chars)"),
  reason_code: z.string().min(3, "Reason code is required"),
  escalate_to: z.string().nullable().optional(),
});

export type DecisionRequest = z.infer<typeof DecisionRequestSchema>;

export const DecisionResponseSchema = z.object({
  decision_id: z.number().int(),
  exception_id: z.number().int(),
  invoice_id: z.number().int(),
  action: z.enum(["APPROVE", "RETURN_TO_VENDOR", "ESCALATE"]),
  actor: z.string(),
  reason_code: z.string(),
  created_at: z.string(),
  exception_status: z.string(),
  graph_resumed: z.boolean(),
  idempotent_replay: z.boolean(),
});

export type DecisionResponse = z.infer<typeof DecisionResponseSchema>;

export const DayVolumeSchema = z.object({
  day: z.string(),
  total: z.number().int(),
  auto_approved: z.number().int(),
});

export const ExceptionTypeCountSchema = z.object({
  type: z.string(),
  severity: z.string(),
  open_count: z.number().int(),
});

export const DashboardSummarySchema = z.object({
  generated_at: z.string(),
  stp_rate: z.number().nullable(),
  invoices_processed: z.number().int(),
  invoices_auto_approved: z.number().int(),
  exceptions_open: z.number().int(),
  aging: z.object({
    on_track: z.number().int(),
    over_4h: z.number().int(),
    over_24h: z.number().int(),
  }),
  volume_by_day: z.array(DayVolumeSchema),
  exception_types: z.array(ExceptionTypeCountSchema),
  cost_per_invoice: z.number().nullable(),
  p95_latency_seconds: z.number().nullable(),
});

export type DashboardSummary = z.infer<typeof DashboardSummarySchema>;
