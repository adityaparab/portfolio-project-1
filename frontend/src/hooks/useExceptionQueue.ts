/** Server state hooks for the exception queue (issue #32).
 * TanStack Query only; keys hierarchical; filters flow as query params. */
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { api } from "~/api/client";
import { InvoiceDetailSchema, QueuePageSchema } from "~/api/schemas";
import type { InvoiceDetail } from "~/api/schemas";

export interface QueueFilters {
  status?: string;
  route?: string;
  exception_type?: string;
  severity?: string;
  sort?: "created_at" | "amount_total" | "sla_due_at" | "severity";
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}

export function useExceptionQueue(filters: QueueFilters) {
  return useQuery({
    queryKey: ["invoices", "queue", filters],
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data, response } = await api.GET("/v1/invoices", {
        params: { query: filters },
      });
      if (!response.ok || !data) throw new Error(`queue HTTP ${response.status}`);
      return QueuePageSchema.parse(data);
    },
  });
}

export function useInvoiceDetail(invoiceId: number | null) {
  return useQuery({
    queryKey: ["invoices", "detail", invoiceId],
    enabled: invoiceId !== null,
    queryFn: async (): Promise<InvoiceDetail> => {
      if (invoiceId === null) throw new Error("no invoice");
      const { data, response } = await api.GET("/v1/invoices/{invoice_id}", {
        params: { path: { invoice_id: invoiceId } },
      });
      if (!response.ok || !data) throw new Error(`detail HTTP ${response.status}`);
      return InvoiceDetailSchema.parse(data);
    },
  });
}
