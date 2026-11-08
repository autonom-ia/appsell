// Caixa "quero novidades desta loja" (#619). O texto é o `store_news` versão 1
// gravado no banco pela migration 0066 (`TEXTOS_V1`): o consentimento guarda o
// sha desse texto como prova, então o que a pessoa vê tem de ser exatamente
// ele. Um teste da API compara as duas cópias. Mudar o texto é migration com
// versão nova, e a versão sobe aqui e em `email_consent_routes.py`.
export const TEXTO_DA_CAIXA_DE_CONSENTIMENTO = "Quero receber por e-mail as novidades desta loja, como produtos e módulos novos. Posso parar quando quiser, pelo link de descadastro em cada e-mail.";

// Ação do token anti-CSRF da gravação. O token é derivado da sessão e da ação
// (`csrfTokenForSession`), então a página que mostra a caixa o calcula no
// servidor e o passa ao componente.
export const ACAO_CSRF_DO_CONSENTIMENTO = "POST:/api/email/consent";

export type OrigemDoConsentimento = "checkout" | "account";
