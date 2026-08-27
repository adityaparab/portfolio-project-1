/** Persona switcher — sets the identity headers the API RBAC checks. */
import { SegmentedControl, Text, Tooltip } from "@mantine/core";
import { PERSONAS, usePersona } from "~/personas/PersonaContext";
import classes from "./PersonaSwitcher.module.css";

export function PersonaSwitcher() {
  const { persona, setPersonaId } = usePersona();
  return (
    <div className={classes.wrapper} data-testid="persona-switcher">
      <Tooltip label={`${persona.name} — ${persona.blurb}`} position="bottom">
        <SegmentedControl
          value={persona.id}
          onChange={setPersonaId}
          data={PERSONAS.map((p) => ({ value: p.id, label: p.name }))}
          classNames={{ root: classes.control, label: classes.label }}
        />
      </Tooltip>
      <Text className={classes.role} component="span" data-testid="persona-role">
        {persona.role}
      </Text>
    </div>
  );
}
