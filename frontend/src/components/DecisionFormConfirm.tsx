/** Four-eyes confirmation modal (issue #32): mirrors the API's rule — the
 * assignee cannot APPROVE; approve shows who must sign instead. */
import { Alert, Badge, Button, Group, Modal, Text } from "@mantine/core";
import { usePersona } from "~/personas/PersonaContext";
import classes from "./DecisionFormConfirm.module.css";

export function DecisionFormConfirm({
  opened,
  action,
  rationale,
  fourEyes,
  submitting,
  onConfirm,
  onCancel,
}: {
  opened: boolean;
  action: string;
  rationale: string;
  fourEyes: boolean;
  submitting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { persona } = usePersona();
  return (
    <Modal opened={opened} onClose={onCancel} title="Confirm decision" classNames={{ content: classes.modal }}>
      {fourEyes && (
        <Alert
          className={classes.fourEyes}
          title="Four-eyes rule"
          icon={<span className={classes.icon}>⬥</span>}
        >
          Whoever works an exception cannot also approve it. If you are the assignee, the API
          rejects this approval — a different persona must sign.
        </Alert>
      )}
      <Group className={classes.row}>
        <Text>Acting as</Text>
        <Group className={classes.persona}>
          <Badge variant="light" className={classes.badgePersona}>
            {persona.name}
          </Badge>
          <Text className={classes.mono}>{persona.role}</Text>
        </Group>
      </Group>
      <Group className={classes.row}>
        <Text>Action</Text>
        <Badge variant="outline" className={classes.mono}>
          {action}
        </Badge>
      </Group>
      <Text className={classes.rationaleLabel}>Rationale (audited)</Text>
      <Text className={classes.rationale}>{rationale || "—"}</Text>
      <Group className={classes.buttons}>
        <Button variant="default" onClick={onCancel}>
          Back
        </Button>
        <Button onClick={onConfirm} loading={submitting} data-testid="confirm-decision">
          Record decision
        </Button>
      </Group>
    </Modal>
  );
}
