/** Decision mutation (issue #32): submit, four-eyes-aware error mapping,
 * targeted cache invalidation (exception + queue lists). */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { notifications } from "@mantine/notifications";
import { api } from "~/api/client";
import { DecisionRequestSchema, DecisionResponseSchema } from "~/api/schemas";
import type { DecisionRequest, DecisionResponse } from "~/api/schemas";

export class DecisionConflictError extends Error {
  constructor(
    public kind: "FOUR_EYES" | "ALREADY_DECIDED",
    public context: Record<string, unknown>,
    message: string,
  ) {
    super(message);
  }
}

export function useDecision(exceptionId: number | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (request: DecisionRequest): Promise<DecisionResponse> => {
      if (exceptionId === null) throw new Error("no exception selected");
      DecisionRequestSchema.parse(request);
      const { data, response } = await api.POST("/v1/exceptions/{exception_id}/decision", {
        params: { path: { exception_id: exceptionId } },
        body: request,
      });
      if (!response.ok) {
        const problem = data as { detail?: unknown } | undefined;
        const detail = problem?.detail;
        if (
          response.status === 409 &&
          detail &&
          typeof detail === "object" &&
          "kind" in detail
        ) {
          const d = detail as { kind: string; message: string; context?: Record<string, unknown> };
          throw new DecisionConflictError(
            d.kind === "FOUR_EYES" ? "FOUR_EYES" : "ALREADY_DECIDED",
            d.context ?? {},
            d.message,
          );
        }
        throw new Error(
          `decision HTTP ${response.status}: ${typeof detail === "string" ? detail : "conflict"}`,
        );
      }
      if (!data) throw new Error("empty decision response");
      return DecisionResponseSchema.parse(data);
    },
    onSuccess: (result) => {
      notifications.show({
        message:
          result.action === "ESCALATE"
            ? `Escalated to ${"director-queue"} — exception stays open`
            : `Decision recorded (${result.action.toLowerCase().replace(/_/g, " ")}) — run resumed`,
        color: result.action === "APPROVE" ? "teal" : "grape",
      });
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (error) => {
      notifications.show({
        message: error instanceof Error ? error.message : "Decision failed",
        color: "red",
      });
    },
  });
}
