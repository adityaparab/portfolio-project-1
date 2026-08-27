/** Shared placeholder for screens landing in their own issues (3.5–3.10). */
import { Card, Text, Title } from "@mantine/core";
import classes from "./ScreenPlaceholder.module.css";

export function ScreenPlaceholder({
  title,
  issue,
}: {
  title: string;
  issue: string;
}) {
  return (
    <div className={classes.page}>
      <Title order={2}>{title}</Title>
      <Card className={classes.card} withBorder>
        <Text className={classes.body}>
          This screen lands with issue {issue}. The API contracts it consumes are live
          (see <code className={classes.code}>/status</code> for a client round-trip).
        </Text>
      </Card>
    </div>
  );
}
