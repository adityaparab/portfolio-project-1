/** Evals (issue #38): the experiment log — versioned metric tables,
 * per-anomaly confusion, tau sweep chart. Reports come from eval/reports/
 * (written by the Phase-5 harness); until then an honest empty state. */
import {
  Badge,
  Card,
  Group,
  Select,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useState } from "react";
import { z } from "zod";
import { api } from "~/api/client";
import classes from "./EvalsPage.module.css";

const ReportSchema = z.object({
  report_version: z.string().optional(),
  generated_at: z.string().optional(),
  model_class: z.string().optional(),
  dataset_version: z.string().optional(),
  metrics: z.record(z.string(), z.object({ value: z.number(), target: z.number().optional() })).optional(),
  confusion: z
    .array(z.object({ code: z.string(), tp: z.number(), fp: z.number(), fn: z.number() }))
    .optional(),
  tau_sweep: z
    .array(
      z.object({
        tau: z.number(),
        stp_rate: z.number(),
        missed_anomaly_rate: z.number(),
      }),
    )
    .optional(),
});
type Report = z.infer<typeof ReportSchema>;

export function EvalsPage() {
  const index = useQuery({
    queryKey: ["evals", "index"],
    queryFn: async () => {
      const { data, response } = await api.GET("/v1/evals/reports");
      if (!response.ok || !data) throw new Error(`evals HTTP ${response.status}`);
      return z.object({ reports: z.array(z.object({ name: z.string() })) }).parse(data);
    },
  });

  const [selected, setSelected] = useState<string | null>(null);
  const activeName = selected ?? index.data?.reports[0]?.name ?? null;

  const report = useQuery({
    queryKey: ["evals", "report", activeName],
    enabled: activeName !== null,
    queryFn: async (): Promise<Report> => {
      if (activeName === null) throw new Error("no report");
      const { data, response } = await api.GET("/v1/evals/reports/{name}", {
        params: { path: { name: activeName } },
      });
      if (!response.ok || !data) throw new Error(`report HTTP ${response.status}`);
      return ReportSchema.parse(data);
    },
  });

  const metrics = report.data?.metrics ?? {};
  const confusion = report.data?.confusion ?? [];
  const sweep = report.data?.tau_sweep ?? [];

  return (
    <div className={classes.page}>
      <Title order={2}>Evals — experiment log</Title>

      {index.data && index.data.reports.length > 0 && (
        <Select
          label="Report"
          data={index.data.reports.map((r) => ({ value: r.name, label: r.name }))}
          value={activeName}
          onChange={setSelected}
          className={classes.select}
        />
      )}

      {report.data && (
        <Group className={classes.meta}>
          <Badge variant="light" className={classes.badgeMeta}>
            {report.data.report_version ?? "unversioned"}
          </Badge>
          {report.data.model_class && <Badge variant="outline">{report.data.model_class}</Badge>}
          {report.data.dataset_version && (
            <Badge variant="outline">{report.data.dataset_version}</Badge>
          )}
          {report.data.generated_at && (
            <Text className={classes.mono}>{report.data.generated_at}</Text>
          )}
        </Group>
      )}

      {Object.keys(metrics).length > 0 && (
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Metrics
          </Title>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Metric</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Target</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {Object.entries(metrics).map(([name, metric]) => {
                const onTarget =
                  metric.target === undefined ? null : metric.value >= metric.target;
                return (
                  <Table.Tr key={name}>
                    <Table.Td>{name}</Table.Td>
                    <Table.Td className={classes.mono}>{metric.value.toFixed(3)}</Table.Td>
                    <Table.Td className={classes.mono}>
                      {metric.target !== undefined ? metric.target.toFixed(2) : "—"}
                    </Table.Td>
                    <Table.Td>
                      {onTarget !== null && (
                        <Badge
                          variant="light"
                          className={onTarget ? classes.badgeOk : classes.badgeDown}
                        >
                          {onTarget ? "meets" : "below"}
                        </Badge>
                      )}
                    </Table.Td>
                  </Table.Tr>
                );
              })}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      {confusion.length > 0 && (
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            Per-anomaly confusion
          </Title>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Code</Table.Th>
                <Table.Th>TP</Table.Th>
                <Table.Th>FP</Table.Th>
                <Table.Th>FN</Table.Th>
                <Table.Th>Recall</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {confusion.map((row) => (
                <Table.Tr key={row.code}>
                  <Table.Td>{row.code.replace(/_/g, " ")}</Table.Td>
                  <Table.Td className={classes.mono}>{row.tp}</Table.Td>
                  <Table.Td className={classes.mono}>{row.fp}</Table.Td>
                  <Table.Td className={classes.mono}>{row.fn}</Table.Td>
                  <Table.Td className={classes.mono}>
                    {row.tp + row.fn > 0 ? (row.tp / (row.tp + row.fn)).toFixed(2) : "—"}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      {sweep.length > 0 && (
        <Card className={classes.card} withBorder>
          <Title order={4} className={classes.cardTitle}>
            τ sweep — STP vs missed-anomaly rate
          </Title>
          <ResponsiveContainer className={classes.chart} height={220}>
            <LineChart data={sweep}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--io-border)" />
              <XAxis dataKey="tau" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <ChartTooltip />
              <Legend />
              <Line dataKey="stp_rate" name="STP rate" stroke="var(--io-primary)" />
              <Line
                dataKey="missed_anomaly_rate"
                name="missed anomalies"
                stroke="var(--io-danger)"
              />
            </LineChart>
          </ResponsiveContainer>
        </Card>
      )}

      {(!index.data || index.data.reports.length === 0) && !index.isError && (
        <Card className={classes.card} withBorder>
          <Text className={classes.muted}>
            No experiment reports yet — the eval harness and golden dataset land with Phase 5
            (#45–#51). This screen reads versioned reports from eval/reports/ the moment they
            exist.
          </Text>
        </Card>
      )}
      {index.isError && <Text className={classes.error}>{String(index.error)}</Text>}
    </div>
  );
}
