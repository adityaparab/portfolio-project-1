/** Run trace hook (issue #36): polls the trace endpoint at one interval. */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api } from "~/api/client";

const TraceEventSchema = z.object({
  seq: z.number().int(),
  actor_type: z.string(),
  actor_id: z.string().nullable(),
  event: z.string(),
  payload: z.record(z.string(), z.unknown()),
  created_at: z.string(),
});

export const RunTraceSchema = z.object({
  run_id: z.number().int(),
  invoice_id: z.number().int(),
  status: z.string(),
  route: z.string().nullable(),
  confidence: z.number().nullable(),
  graph_version: z.string(),
  node_trace: z.array(z.string()),
  timeline: z.array(TraceEventSchema),
});

export type RunTrace = z.infer<typeof RunTraceSchema>;

export function useRunTrace(runId: number | null) {
  return useQuery({
    queryKey: ["runs", "trace", runId],
    enabled: runId !== null,
    refetchInterval: (query) => {
      const data = query.state.data as RunTrace | undefined;
      // settled runs stop polling; paused runs (HITL) also settle
      return data && (data.status === "COMPLETED" || data.status === "REJECTED") ? false : 2000;
    },
    queryFn: async (): Promise<RunTrace> => {
      if (runId === null) throw new Error("no run");
      const { data, response } = await api.GET("/v1/runs/{run_id}/trace", {
        params: { path: { run_id: runId } },
      });
      if (!response.ok || !data) throw new Error(`trace HTTP ${response.status}`);
      return RunTraceSchema.parse(data);
    },
  });
}
