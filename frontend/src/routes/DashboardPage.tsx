/** Dashboard (Dan's view, issue #33): KPI cards + Recharts, every chart
 * segment drill-throughs to a pre-filtered queue. Metrics are displayed,
 * never recomputed — the API computes everything. */
import { Badge, Card, Group, Skeleton, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
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
import { useDashboardSummary } from "~/hooks/useDashboard";
import classes from "./DashboardPage.module.css";

export function DashboardPage() {
  const summary = useDashboardSummary();
  const data = summary.data;

  return (
    <div className={classes.page}>
      <Title order={2}>Dashboard</Title>

      <div className={classes.kpis}>
        <Card className={classes.kpi} withBorder>
          <Text className={classes.kpiLabel}>Straight-through processing</Text>
          {data ? (
            <Text className={classes.kpiValue}>{(data.stp_rate ?? 0).toFixed(1)}%</Text>
          ) : (
            <Skeleton className={classes.skeleton} />
          )}
          {data && (
            <Text className={classes.kpiHint}>
              {data.invoices_auto_approved} auto of {data.invoices_processed} processed
            </Text>
          )}
        </Card>
        <Card className={classes.kpi} withBorder>
          <Text className={classes.kpiLabel}>Open exceptions</Text>
          {data ? (
            <Link to="/queue" className={classes.kpiLink}>
              {data.exceptions_open}
            </Link>
          ) : (
            <Skeleton className={classes.skeleton} />
          )}
          {data && (
            <Text className={classes.kpiHint}>
              {data.aging.over_24h} past 24h · {data.aging.over_4h} past 4h
            </Text>
          )}
        </Card>
        <Card className={classes.kpi} withBorder>
          <Text className={classes.kpiLabel}>Cost / invoice</Text>
          <Text className={classes.kpiValue}>—</Text>
          <Text className={classes.kpiHint}>LiteLLM spend joins with #43</Text>
        </Card>
        <Card className={classes.kpi} withBorder>
          <Text className={classes.kpiLabel}>p95 latency</Text>
          <Text className={classes.kpiValue}>—</Text>
          <Text className={classes.kpiHint}>OTel metrics land with #43</Text>
        </Card>
      </div>

      <div className={classes.charts}>
        <Card className={classes.chartCard} withBorder>
          <Title order={4} className={classes.chartTitle}>
            Volume — 14 days (auto vs exceptions)
          </Title>
          {data ? (
            <ResponsiveContainer className={classes.chart} height={220}>
              <LineChart data={data.volume_by_day}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--io-border)" />
                <XAxis dataKey="day" tick={{ fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11 }} />
                <ChartTooltip />
                <Legend />
                <Line dataKey="total" name="processed" stroke="var(--io-accent)" />
                <Line dataKey="auto_approved" name="auto-approved" stroke="var(--io-primary)" />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <Skeleton className={classes.skeleton} height={220} />
          )}
        </Card>

        <Card className={classes.chartCard} withBorder>
          <Title order={4} className={classes.chartTitle}>
            Open exceptions by type — click through
          </Title>
          {data && data.exception_types.length > 0 ? (
            <div className={classes.typeList}>
              {data.exception_types.map((row) => (
                <Link
                  key={row.type}
                  to={`/queue?exception_type=${row.type}`}
                  className={classes.typeRow}
                >
                  <Badge
                    variant="light"
                    className={row.severity === "HIGH" ? classes.badgeHigh : classes.badgeMedium}
                  >
                    {row.type.replace(/_/g, " ")}
                  </Badge>
                  <Text className={classes.typeCount}>{row.open_count}</Text>
                </Link>
              ))}
            </div>
          ) : (
            <Group className={classes.empty}>
              <Text className={classes.muted}>No open exceptions — clean queue.</Text>
            </Group>
          )}
        </Card>
      </div>
      {summary.isError && (
        <Text className={classes.error}>Metrics failed to load: {String(summary.error)}</Text>
      )}
    </div>
  );
}
