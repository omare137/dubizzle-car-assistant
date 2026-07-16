import { createContext, useContext, useState, type ReactNode } from "react";
import type { SessionResponse } from "@/lib/api";

interface SessionContextValue {
  session: SessionResponse | null;
  setSession: (s: SessionResponse | null) => void;
  clearSession: () => void;
}

const SessionContext = createContext<SessionContextValue | undefined>(undefined);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionResponse | null>(null);
  return (
    <SessionContext.Provider
      value={{ session, setSession, clearSession: () => setSession(null) }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}
