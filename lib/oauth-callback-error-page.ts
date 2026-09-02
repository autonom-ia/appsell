// A browser landing on /auth/callback is a person, not an API client. Answering
// with raw JSON ({"error":"invalid_oauth_callback"}) reads as a broken site and
// gives them nothing to do. This renders a plain, self-contained page with a
// neutral message and a way back to the login.
//
// The message is the same for every failure but one: the outcome code is
// useful to whoever reads the server log, not to an anonymous visitor, and
// varying the text would turn the page into a configuration oracle. The code is
// still emitted as an HTML comment so a support conversation can quote it.
//
// The one exception is the unverified e-mail (#301). The provider has already
// authenticated the person; what is missing is a click on the confirmation
// message the sign-up sent. Telling them "sign in again" is a closed loop —
// signing in again produces exactly the same refusal — so this case names the
// way out. It reveals only the person's own state, never configuration.

type Messages = { readonly title: string; readonly body: string; readonly cta: string };

const NEUTRAL: Messages = {
  title: "Não foi possível concluir o acesso",
  // Neutral on purpose: this page also serves configuration and upstream
  // failures, so it must not assert anything about the account.
  body: "Entre novamente para continuar. Se o problema persistir, fale com o suporte.",
  cta: "Entrar novamente"
};

const EMAIL_UNVERIFIED: Messages = {
  title: "Confirme seu e-mail",
  body: "Sua conta foi criada, mas o e-mail ainda não foi confirmado. Abra a mensagem que o cadastro enviou para o seu endereço, clique no link de confirmação e depois entre de novo. Não achou? Olhe a caixa de spam.",
  cta: "Já confirmei, entrar"
};

export function messagesForOutcome(outcome: string): Messages {
  return outcome === "identity_email_unverified" ? EMAIL_UNVERIFIED : NEUTRAL;
}

export function isBrowserNavigation(accept: string | null): boolean {
  return typeof accept === "string" && accept.includes("text/html");
}

export function renderCallbackErrorPage(outcome: string, loginPath = "/auth/login"): string {
  // Only the sanitized code reaches the document, and only inside a comment.
  const safeOutcome = outcome.replace(/[^a-z0-9_.-]/gi, "").slice(0, 64);
  const MESSAGES = messagesForOutcome(outcome);
  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>${MESSAGES.title}</title>
<style>
:root { color-scheme: light dark; }
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background:#f6f7f9; color:#101828; padding:24px; }
main { max-width:420px; text-align:center; background:#fff; padding:40px 32px; border-radius:16px;
  box-shadow:0 1px 3px rgba(16,24,40,.1), 0 1px 2px rgba(16,24,40,.06); }
h1 { font-size:20px; line-height:1.4; margin:0 0 12px; }
p { font-size:15px; line-height:1.6; color:#475467; margin:0 0 28px; }
a { display:inline-block; background:#1e3a8a; color:#fff; text-decoration:none; font-weight:600;
  font-size:15px; padding:12px 24px; border-radius:8px; }
@media (prefers-color-scheme: dark) {
  body { background:#101828; color:#f9fafb; }
  main { background:#1d2939; box-shadow:none; }
  p { color:#98a2b3; }
}
</style>
</head>
<body>
<main>
<h1>${MESSAGES.title}</h1>
<p>${MESSAGES.body}</p>
<a href="${loginPath}">${MESSAGES.cta}</a>
</main>
<!-- ${safeOutcome} -->
</body>
</html>`;
}
