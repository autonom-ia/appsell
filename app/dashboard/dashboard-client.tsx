"use client";

import { useEffect, useState } from "react";

interface SessionView {
  authenticated: true;
  subject: string;
  createdAt: number;
  expiresAt: number;
}

export default function DashboardClient() {
  const [session, setSession] = useState<SessionView | null>(null);

  useEffect(() => {
    let active = true;
    fetch("/api/session", { cache: "no-store", credentials: "same-origin" })
      .then(async (response) => {
        if (!response.ok) {
          window.location.replace("/");
          return null;
        }
        return response.json() as Promise<SessionView>;
      })
      .then((value) => {
        if (active && value) setSession(value);
      })
      .catch(() => window.location.replace("/"));
    return () => {
      active = false;
    };
  }, []);

  if (!session) return <main><p>Validando sessão…</p></main>;

  return (
    <main>
      <h1>Dashboard protegido</h1>
      <p>Sessão ativa para <strong>{session.subject}</strong>.</p>
      <form action="/auth/logout" method="post"><button type="submit">Sair</button></form>
    </main>
  );
}
