"use client";

import { useEffect, useState, type FormEvent } from "react";

interface DemoInspectionProjection {
  inspection: { id: string; started_at: string; expires_at: string };
  seller: { id: string; slug: string; display_name: string; status: string };
  product: { id: string; slug: string; title: string; status: string };
  publication: { id: string; version: number; schema_version: number; published_at: string };
  courses: Array<{ id: string; title: string; position: number }>;
}

interface InspectionAccess {
  inspection: DemoInspectionProjection | null;
  ticket_status: "inactive" | "active" | "expired";
  csrf: { start: string; revoke: string };
}

type AccessState =
  | { status: "loading" }
  | { status: "denied" }
  | { status: "unavailable" }
  | { status: "ready"; value: InspectionAccess };

export function DemoInspectionPanel({ feedback }: { feedback: "started" | "revoked" | null }) {
  const [access, setAccess] = useState<AccessState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/platform/demo-inspections", {
      cache: "no-store",
      credentials: "same-origin",
      signal: controller.signal
    }).then(async (response) => {
      if (response.status === 401 || response.status === 403) {
        setAccess({ status: "denied" });
        return;
      }
      if (!response.ok) {
        setAccess({ status: "unavailable" });
        return;
      }
      const parsed = parseAccess(await response.json() as unknown);
      setAccess(parsed ? { status: "ready", value: parsed } : { status: "unavailable" });
    }).catch(() => {
      if (!controller.signal.aborted) setAccess({ status: "unavailable" });
    });
    return () => controller.abort();
  }, []);

  return (
    <>
      <header className="page-header inspection-header">
        <div>
          <p className="eyebrow">Superadmin · leitura controlada</p>
          <h1>Inspeção da tenant demo</h1>
          <p>Escopo fixo, tempo limitado e trilha append-only. Este fluxo não impersona o seller e não concede membership.</p>
        </div>
        <span className="inspection-safety-badge">LOCAL/CI · READ-ONLY</span>
      </header>

      {feedback === "started" ? <p className="inspection-feedback success" role="status">Inspeção iniciada e auditada.</p> : null}
      {feedback === "revoked" ? <p className="inspection-feedback" role="status">Inspeção encerrada e token revogado.</p> : null}

      {access.status === "loading" ? (
        <section className="inspection-card" aria-live="polite">
          <p className="eyebrow">Validando grant</p>
          <h2>Carregando acesso seguro…</h2>
        </section>
      ) : null}
      {access.status === "denied" ? <DeniedInspection/> : null}
      {access.status === "unavailable" ? <UnavailableInspection/> : null}
      {access.status === "ready" && access.value.inspection ? (
        <ActiveInspection projection={access.value.inspection} csrf={access.value.csrf.revoke}/>
      ) : null}
      {access.status === "ready" && !access.value.inspection ? (
        <StartInspection
          csrf={access.value.csrf.start}
          expired={access.value.ticket_status === "expired"}
        />
      ) : null}
    </>
  );
}

function DeniedInspection() {
  return (
    <section className="inspection-denied" aria-labelledby="inspection-denied-title">
      <p className="eyebrow">Acesso de plataforma</p>
      <h2 id="inspection-denied-title">Inspeção indisponível</h2>
      <p>Esta identidade não possui um grant ativo de platform_superadmin. E-mail, rota e sessão local não concedem esse papel.</p>
      <div className="blocked-card"><span className="blocked-icon" aria-hidden="true">!</span><div><strong>Identity real ainda bloqueado</strong><p>Este slice comprova apenas o mecanismo Local/CI. Nenhuma conta real, VPS, DNS ou produção foi ativada.</p></div></div>
    </section>
  );
}

function UnavailableInspection() {
  return (
    <section className="inspection-denied" aria-labelledby="inspection-unavailable-title">
      <p className="eyebrow">Falha fechada</p>
      <h2 id="inspection-unavailable-title">Não foi possível verificar o acesso</h2>
      <p>A API de inspeção está indisponível. Nenhum ticket ou capability foi concedido.</p>
    </section>
  );
}

function StartInspection({ csrf, expired }: { csrf: string; expired: boolean }) {
  const submission = useInspectionSubmission("started");
  return (
    <section className="inspection-card" aria-labelledby="start-inspection-title">
      <div>
        <p className="eyebrow">Nova inspeção</p>
        <h2 id="start-inspection-title">Justifique antes de abrir</h2>
        <p>A projeção contém somente metadados operacionais da Autonom.ia Demo. Conteúdo privado, PII de compradores, billing e secrets permanecem fora do alcance.</p>
      </div>
      {expired ? <p className="inspection-feedback warning" role="status">O ticket anterior expirou ou foi revogado. Inicie outro somente se ainda houver necessidade.</p> : null}
      <form action="/api/platform/demo-inspections" method="post" onSubmit={submission.submit}>
        <input type="hidden" name="_csrf" value={csrf}/>
        <label>Motivo operacional
          <textarea name="reason" minLength={8} maxLength={500} required defaultValue="Demonstração comercial supervisionada da tenant Autonom.ia Demo."/>
          <span>O motivo será persistido na auditoria e não poderá ser alterado.</span>
        </label>
        <label>Duração máxima
          <select name="ttl_seconds" defaultValue="300">
            <option value="300">5 minutos</option>
            <option value="600">10 minutos</option>
            <option value="900">15 minutos</option>
          </select>
        </label>
        <button className="primary-button" type="submit" disabled={submission.pending}>
          {submission.pending ? "Iniciando inspeção…" : "Iniciar inspeção read-only"}
        </button>
        {submission.error ? <p className="form-error" role="alert">{submission.error}</p> : null}
      </form>
    </section>
  );
}

function ActiveInspection({ projection, csrf }: { projection: DemoInspectionProjection; csrf: string }) {
  const submission = useInspectionSubmission("revoked");
  return (
    <div className="inspection-grid">
      <section className="inspection-card inspection-active" aria-labelledby="active-inspection-title">
        <div>
          <p className="eyebrow">Ticket ativo</p>
          <h2 id="active-inspection-title">{projection.seller.display_name}</h2>
          <p>Expira em <time dateTime={projection.inspection.expires_at}>{formatTimestamp(projection.inspection.expires_at)}</time>. O seller vem do servidor; não pode ser trocado pela URL ou pelo formulário.</p>
        </div>
        <dl className="inspection-facts">
          <div><dt>Tenant</dt><dd>{projection.seller.slug}</dd></div>
          <div><dt>Status</dt><dd>{projection.seller.status}</dd></div>
          <div><dt>Produto publicado</dt><dd>{projection.product.title}</dd></div>
          <div><dt>Snapshot</dt><dd>v{projection.publication.version} · schema {projection.publication.schema_version}</dd></div>
          <div><dt>Cursos</dt><dd>{projection.courses.length}</dd></div>
        </dl>
      </section>

      <section className="inspection-card inspection-revoke" aria-labelledby="revoke-inspection-title">
        <div>
          <p className="eyebrow">Encerramento explícito</p>
          <h2 id="revoke-inspection-title">Revogar agora</h2>
          <p>A revogação invalida o token imediatamente e gera outro evento append-only.</p>
        </div>
        <form action="/api/platform/demo-inspections/revoke" method="post" onSubmit={submission.submit}>
          <input type="hidden" name="_csrf" value={csrf}/>
          <label>Motivo do encerramento
            <textarea name="reason" minLength={8} maxLength={500} required defaultValue="Inspeção comercial concluída com segurança."/>
          </label>
          <button className="danger-button" type="submit" disabled={submission.pending}>
            {submission.pending ? "Revogando acesso…" : "Encerrar e revogar acesso"}
          </button>
          {submission.error ? <p className="form-error" role="alert">{submission.error}</p> : null}
        </form>
      </section>
    </div>
  );
}

function useInspectionSubmission(expectedStatus: "started" | "revoked") {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    const form = event.currentTarget;
    const body = new URLSearchParams();
    for (const [name, value] of new FormData(form).entries()) {
      if (typeof value !== "string") {
        setError("O formulário contém um valor inválido.");
        return;
      }
      body.append(name, value);
    }
    setPending(true);
    setError("");
    try {
      const response = await fetch(form.action, {
        method: "POST",
        body,
        credentials: "same-origin",
        redirect: "follow"
      });
      if (!response.ok || !response.redirected) throw new Error("inspection_submit_failed");
      const destination = new URL(response.url);
      if (
        destination.origin !== window.location.origin ||
        destination.pathname !== "/platform/demo-inspection" ||
        destination.searchParams.get("status") !== expectedStatus
      ) {
        throw new Error("inspection_redirect_invalid");
      }
      window.location.assign(destination.href);
    } catch {
      setPending(false);
      setError("Não foi possível concluir a operação com segurança. Tente novamente.");
    }
  }

  return { pending, error, submit };
}

function parseAccess(value: unknown): InspectionAccess | null {
  if (!isRecord(value) || value.authorized !== true || !isRecord(value.csrf) ||
      typeof value.csrf.start !== "string" || typeof value.csrf.revoke !== "string" ||
      !["inactive", "active", "expired"].includes(String(value.ticket_status))) return null;
  const inspection = value.inspection === null ? null : parseProjection(value.inspection);
  if (value.inspection !== null && !inspection) return null;
  if ((value.ticket_status === "active") !== Boolean(inspection)) return null;
  return {
    inspection,
    ticket_status: value.ticket_status as InspectionAccess["ticket_status"],
    csrf: { start: value.csrf.start, revoke: value.csrf.revoke }
  };
}

function parseProjection(value: unknown): DemoInspectionProjection | null {
  if (!isRecord(value) || !isRecord(value.inspection) || !isRecord(value.seller) ||
      !isRecord(value.product) || !isRecord(value.publication) || !Array.isArray(value.courses)) {
    return null;
  }
  const stringFields = [
    value.inspection.id, value.inspection.started_at, value.inspection.expires_at,
    value.seller.id, value.seller.slug, value.seller.display_name, value.seller.status,
    value.product.id, value.product.slug, value.product.title, value.product.status,
    value.publication.id, value.publication.published_at
  ];
  if (stringFields.some((field) => typeof field !== "string") ||
      typeof value.publication.version !== "number" ||
      typeof value.publication.schema_version !== "number" ||
      !value.courses.every((course) => isRecord(course) && typeof course.id === "string" &&
        typeof course.title === "string" && typeof course.position === "number")) return null;
  return value as unknown as DemoInspectionProjection;
}

function formatTimestamp(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf())
    ? "horário indisponível"
    : new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" }).format(parsed);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
