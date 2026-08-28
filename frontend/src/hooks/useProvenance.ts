/** Provenance hook (issue #37): the full export, audit-role gated. */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "~/api/client";

const ProvenanceEventSchema = z.object({
  seq: z.number().int(),
  actor_type: z.string(),
  actor_id: z.string().nullable(),
  event: z.string(),
  payload: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  versions: z.record(z.string(), z.unknown()).nullable(),
  policy_version: z.string().nullable(),
  prompt_template_version: z.string().nullable(),
});

export const ProvenancePackageSchema = z.object({
  invoice_id: z.number().int(),
  generated_at: z.string(),
  runs: z.array(
    z.object({
      run_id: z.number().int(),
      graph_version: z.string(),
      model_versions: z.record(z.string(), z.unknown()),
      route: z.string().nullable(),
      status: z.string(),
      confidence: z.number().nullable(),
      started_at: z.string(),
      finished_at: z.string().nullable(),
    }),
  ),
  exceptions: z.array(
    z.object({
      exception_id: z.number().int(),
      run_id: z.number().int().nullable(),
      type: z.string(),
      severity: z.string(),
      status: z.string(),
      evidence: z.record(z.string(), z.unknown()),
      recommendation: z.record(z.string(), z.unknown()).nullable(),
    }),
  ),
  decisions: z.array(
    z.object({
      decision_id: z.number().int(),
      exception_id: z.number().int(),
      actor_user: z.string(),
      action: z.string(),
      rationale: z.string(),
      reason_code: z.string(),
      created_at: z.string(),
    }),
  ),
  ledger: z.array(ProvenanceEventSchema),
});

export type ProvenancePackage = z.infer<typeof ProvenancePackageSchema>;

export function useProvenance(invoiceId: number | null) {
  return useQuery({
    queryKey: ["invoices", "provenance", invoiceId],
    enabled: invoiceId !== null,
    retry: false,
    queryFn: async (): Promise<ProvenancePackage> => {
      if (invoiceId === null) throw new Error("no invoice");
      const { data, response } = await api.GET("/v1/invoices/{invoice_id}/provenance", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (response.status === 403) {
        throw new Error("Provenance export requires the audit or platform persona");
      }
      if (!response.ok || !data) throw new Error(`provenance HTTP ${response.status}`);
      return ProvenancePackageSchema.parse(data);
    },
  });
}
