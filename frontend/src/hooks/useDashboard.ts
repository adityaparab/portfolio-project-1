/** Dashboard aggregates hook (issue #33): server-computed, auto-refreshed. */
import { useQuery } from "@tanstack/react-query";
import { api } from "~/api/client";
import { DashboardSummarySchema } from "~/api/schemas";
import type { DashboardSummary } from "~/api/schemas";

export function useDashboardSummary(days = 14) {
  return useQuery({
    queryKey: ["metrics", "summary", days],
    refetchInterval: 30_000,
    queryFn: async (): Promise<DashboardSummary> => {
      const { data, response } = await api.GET("/v1/metrics/summary", {
        params: { query: { days } },
      });
      if (!response.ok || !data) throw new Error(`summary HTTP ${response.status}`);
      return DashboardSummarySchema.parse(data);
    },
  });
}
