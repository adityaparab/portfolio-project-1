/** Upload mutation (issue #34): multipart POST to the ingest endpoint.
 * Machine channel: service bearer token (personas are for the RBAC reads). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import { z } from "zod";

export const UploadReceiptSchema = z.object({
  invoice_id: z.number().int(),
  run_id: z.number().int(),
  content_hash: z.string(),
  status: z.string(),
  duplicate: z.boolean(),
});
export type UploadReceipt = z.infer<typeof UploadReceiptSchema>;

const SERVICE_TOKEN = "dev-service-token"; // demo stub (matches compose defaults)

export function useUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<UploadReceipt> => {
      const body = new FormData();
      body.append("upload", file, file.name);
      const response = await fetch("/api/v1/invoices", {
        method: "POST",
        headers: { Authorization: `Bearer ${SERVICE_TOKEN}` },
        body,
      });
      const payload: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const detail =
          payload && typeof payload === "object" && "detail" in payload
            ? String((payload as { detail: unknown }).detail)
            : `HTTP ${response.status}`;
        throw new Error(detail);
      }
      return UploadReceiptSchema.parse(payload);
    },
    onSuccess: (receipt) => {
      notifications.show({
        message: receipt.duplicate
          ? "Duplicate content hash — rejected (the original invoice already exists)"
          : `Accepted — invoice #${receipt.invoice_id}, run #${receipt.run_id} processing`,
        color: receipt.duplicate ? "red" : "teal",
      });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error) => {
      notifications.show({ message: `Upload failed: ${String(error)}`, color: "red" });
    },
  });
}
