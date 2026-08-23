# Plano de implementação — jornada de publicação Seller P0/P1

- Issue: #188 — P0/P1: jornada guiada de publicação para sellers
- Branch: `codex/publish-ux-p0-p1`
- Base: `6333a8e`
- Escopo: fluxo seller de criar produto, preparar conteúdo, configurar oferta, revisar e publicar catálogo
- Limite: não fazer merge, deploy, cobrança, checkout real, secrets ou alteração de produção neste slice

## Decisão de produto adotada para este slice

Publicar catálogo e ativar vendas são estados diferentes. O fluxo de publicação deve permitir preparar e publicar o catálogo sem prometer checkout ou recebimento. Ativação de vendas fica fora do caminho principal até a configuração financeira estar disponível.

## P0 — remover becos sem saída

- [x] P0.1 Separar salvamento por domínio: Produto/curso não envia nem valida campos ocultos da Oferta.
- [x] P0.2 Criar checklist/progresso de publicação com estado incompleto, pronto, pendente e bloqueado.
- [x] P0.3 Fazer o CTA principal seguir o próximo passo real; não expor Ativar oferta como pré-requisito de Publicar catálogo.
- [x] P0.4 Exibir erro com causa, correção e navegação/foco no campo ou etapa responsável.
- [x] P0.5 Preservar tenant explícito e estado salvo ao navegar entre etapas.
- [x] P0.6 Testar Produto novo: primeiro salvamento, Oferta ausente, Oferta salva, publicação e tentativa de ativação fora de ordem.

## P1 — melhorar estados assíncronos e revisão

- [x] P1.1 Exibir a máquina de estados do material: preparando, enviando, confirmando, verificando, pronto, falho e expirado.
- [x] P1.2 Evitar espera infinita: timeout de UX, mensagem recuperável e retry sem perder o upload confirmado.
- [x] P1.3 Permitir continuar editando enquanto a verificação está em andamento; bloquear somente a publicação quando necessário.
- [x] P1.4 Ajustar Preview para declarar o que mostra e incluir resumo do currículo/materiais; não expor nome físico do arquivo ao aluno.
- [x] P1.5 Testar upload real do fluxo local/integrado, estados de falha/retry, Preview, responsividade e acessibilidade.

## Revisores e gates

- [x] Revisor independente P0: P0/P1/P2/P3 = 0 no review final.
- [x] Revisor independente P1: P0/P1/P2/P3 = 0 no review final.
- [x] Testes unitários e de integração verdes; PostgreSQL head 0028 passou.
- [x] E2E real do fluxo completo verde em Chromium; WebKit não foi necessário para este gate.
- [x] Smoke renderizado sem overflow nas larguras cobertas, com foco/teclado e acessibilidade do uploader verificados.
- [x] Checklist final pronto para entrega ao usuário.
- [x] Aprovação explícita solicitada antes de merge/deploy.
