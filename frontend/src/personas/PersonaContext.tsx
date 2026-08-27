/**
 Persona identity — the single source for the API's RBAC headers (#31).
 The switcher changes the persona; every request through api/client.ts
 carries X-IO-User / X-IO-Role. Roles mirror api/auth.py exactly.
*/
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { setApiIdentityHeaders } from "~/api/client";

export const ROLES = ["analyst", "manager", "audit", "platform"] as const;
export type Role = (typeof ROLES)[number];

export interface Persona {
  id: string;
  name: string;
  role: Role;
  blurb: string;
}

export const PERSONAS: readonly Persona[] = [
  { id: "maria", name: "Maria", role: "analyst", blurb: "Works the exception queue" },
  { id: "dan", name: "Dan", role: "manager", blurb: "Dashboards & approvals" },
  { id: "priya", name: "Priya", role: "audit", blurb: "Provenance & audit trail" },
  { id: "platform", name: "Platform Eng", role: "platform", blurb: "Everything" },
] as const;

interface PersonaState {
  persona: Persona;
  setPersonaId: (id: string) => void;
  /** The exact headers the API RBAC checks (api/auth.py). */
  headers: Record<string, string>;
}

const PersonaContext = createContext<PersonaState | null>(null);

const STORAGE_KEY = "invoiceops.persona";

function initialPersona(): Persona {
  const stored = window.localStorage.getItem(STORAGE_KEY);
  return PERSONAS.find((p) => p.id === stored) ?? PERSONAS[0];
}

export function PersonaProvider({ children }: { children: ReactNode }) {
  const [persona, setPersona] = useState<Persona>(initialPersona);

  const setPersonaId = useCallback((id: string) => {
    const next = PERSONAS.find((p) => p.id === id);
    if (!next) return;
    window.localStorage.setItem(STORAGE_KEY, next.id);
    setPersona(next);
  }, []);

  const headers = useMemo(
    () => ({
      "X-IO-User": `${nextUser(persona)}`,
      "X-IO-Role": persona.role,
    }),
    [persona],
  );

  useEffect(() => {
    setApiIdentityHeaders(headers); // every request carries the persona
  }, [headers]);

  const value = useMemo(
    () => ({ persona, setPersonaId, headers }),
    [persona, setPersonaId, headers],
  );
  return <PersonaContext.Provider value={value}>{children}</PersonaContext.Provider>;
}

function nextUser(persona: Persona): string {
  return `${persona.id}@invoiceops`;
}

export function usePersona(): PersonaState {
  const ctx = useContext(PersonaContext);
  if (!ctx) throw new Error("usePersona requires PersonaProvider");
  return ctx;
}
