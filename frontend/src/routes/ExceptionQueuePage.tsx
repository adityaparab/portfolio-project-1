/** Exception Review — the queue view (issue #32, Maria's primary screen). */
import {
  Badge,
  Card,
  LoadingOverlay,
  SegmentedControl,
  Select,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useExceptionQueue } from "~/hooks/useExceptionQueue";
import { formatMoney, formatAging } from "~/lib/format";
import classes from "./ExceptionQueuePage.module.css";

const TYPE_OPTIONS = [
  "PRICE_MM",
  "QTY_MM",
  "MISSING_PO",
  "BANK_CHANGE",
  "CCY_MM",
  "TAX_ERR",
  "MATH_ERR",
  "STALE_PO",
  "APPROVAL_REQUIRED",
  "DUP_NEAR",
  "DUP_EXACT",
].map((value) => ({ value, label: value.replace(/_/g, " ") }));

export function ExceptionQueuePage() {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const exceptionType = params.get("exception_type") ?? "";
  const severity = params.get("severity") ?? "";
  const sort = (params.get("sort") ?? "sla_due_at") as "sla_due_at" | "severity" | "created_at";

  const queue = useExceptionQueue({
    exception_type: exceptionType || undefined,
    severity: severity || undefined,
    sort,
    order: "asc",
    limit: 50,
  });

  const rows = queue.data?.items ?? [];

  return (
    <div className={classes.page}>
      <div className={classes.header}>
        <Title order={2}>Exception Review</Title>
        <SegmentedControl
          value={sort}
          onChange={(value) => setParams({ exception_type: exceptionType, severity, sort: value })}
          data={[
            { value: "sla_due_at", label: "SLA aging" },
            { value: "severity", label: "Severity" },
            { value: "created_at", label: "Newest" },
          ]}
          classNames={{ root: classes.sortControl }}
        />
      </div>

      <div className={classes.filters}>
        <Select
          placeholder="Exception type"
          data={TYPE_OPTIONS}
          value={exceptionType || null}
          onChange={(value) =>
            setParams({ exception_type: value ?? "", severity, sort })
          }
          clearable
          className={classes.filterSelect}
        />
        <Select
          placeholder="Severity"
          data={["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((value) => ({ value, label: value }))}
          value={severity || null}
          onChange={(value) => setParams({ exception_type: exceptionType, severity: value ?? "", sort })}
          clearable
          className={classes.filterSelect}
        />
      </div>

      <Card className={classes.tableCard} withBorder>
        <LoadingOverlay visible={queue.isLoading} />
        {queue.isError && (
          <Text className={classes.error}>Queue failed to load: {String(queue.error)}</Text>
        )}
        {!queue.isLoading && !queue.isError && rows.length === 0 && (
          <Text className={classes.empty}>No exceptions match — the queue is clear.</Text>
        )}
        {rows.length > 0 && (
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Invoice</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Severity</Table.Th>
                <Table.Th>Amount</Table.Th>
                <Table.Th>SLA</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Assignee</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((item) => (
                <Table.Tr
                  key={item.invoice_id}
                  className={classes.row}
                  onClick={() => navigate(`/queue/${item.invoice_id}`)}
                >
                  <Table.Td className={classes.mono}>{item.invoice_number ?? `#${item.invoice_id}`}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" className={severityClass(item.exception?.severity)}>
                      {item.exception?.type.replace(/_/g, " ") ?? item.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{item.exception?.severity ?? "—"}</Table.Td>
                  <Table.Td className={classes.mono}>
                    {item.currency} {formatMoney(item.amount_total)}
                  </Table.Td>
                  <Table.Td>
                    {item.exception?.sla_overdue_seconds != null &&
                    item.exception.sla_overdue_seconds > 0 ? (
                      <Badge variant="light" className={classes.badgeDown}>
                        {formatAging(item.exception.sla_overdue_seconds)} overdue
                      </Badge>
                    ) : (
                      <Text className={classes.muted}>on track</Text>
                    )}
                  </Table.Td>
                  <Table.Td>{item.exception?.status ?? item.run?.status ?? item.status}</Table.Td>
                  <Table.Td>{item.exception?.assignee ?? "unassigned"}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}

function severityClass(severity: string | undefined): string {
  switch (severity) {
    case "CRITICAL":
      return classes.badgeCritical;
    case "HIGH":
      return classes.badgeHigh;
    case "MEDIUM":
      return classes.badgeMedium;
    default:
      return classes.badgeLow;
  }
}
