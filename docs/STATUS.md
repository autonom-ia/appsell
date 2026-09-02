# STATUS — Appsell

Última atualização documental: **2026-09-02**.

Este é o snapshot operacional atual. Para detalhes da release e dos aceites,
consulte [PRODUCTION_CURRENT.md](PRODUCTION_CURRENT.md). Auditorias antigas em
`docs/audit/` são históricas e não substituem a evidência do SHA implantado.

## Indicadores

| Indicador | Estado | Evidência |
|---|:---:|---|
| Deploy | 🟢 | api em `581a592`, worker e sandbox em `d5e00ec`; web em `9655a13` (02/09, só o Next) |
| Banco | 🟢 | head `0044_pdf_de_trezentas_paginas`, `schema_ready() = true` |
| API | 🟢 | `/health/ready`: database, storage e pdf sandbox `true` |
| PDF do vendedor | 🟢 | playbook de 83 páginas, 32 links lidos (20 índice, 12 vídeo) |
| Teto de páginas | 🟢 | 300, em nove camadas — medido com documento real |
| Vídeo do PDF | 🟡 | toca, mas o aluno espera ~2 s no primeiro clique de cada vídeo |
| Vídeo longo | 🔴 | duração de 4 h é aceita e nunca fica pronta; envio limitado a 100 MiB |
| Logs da API | 🔴 | não gravam nada ([#312](https://github.com/autonom-ia/appsell/issues/312)) — "0 erros" não é evidência |
| Página pública | 🟢 | `9655a13`: passagem entre atos por tempo (Decisão 018), contraste medido sob os glifos em 8 telas × 3 atos, `robots.txt` e `sitemap.xml` no ar |
| Contato da página | 🟢 | SMTP comercial configurado no `web`; envio real confirmado recebido em 01/09 |
| Navegação no celular | 🟢 | sem menu por decisão ([#347](https://github.com/autonom-ia/appsell/issues/347)): em 320px não cabe, e a página tem CTA fixo o tempo todo |
| Suítes fora do CI | 🟡 | 5 arquivos `bff-*`, inclusive o de isolamento entre lojas ([#266](https://github.com/autonom-ia/appsell/issues/266)) |

## Produção comprovada

- Domínio: `https://sell.autonomia.site`.
- Banco: `appsell_native_90`; migration head `0032_platform_superadmin_catalog`.
- Tenant real: Autonom.ia (`autonomia`) e Hub2You (`hub2you`).
- Hierarquia: `comercial@autonomia.site` como `platform_superadmin`; cada
  vendedor como `seller_owner` no próprio tenant; comprador como identidade
  global com entitlement.
- Produto real verificado: Hub2You / `IA na prática`.
- A edição segue Produto → Currículo → Oferta → Preview → Publicar.
- Publicação cria snapshot; ativação de vendas é separada e ocorre em Publicar.

## Aceite final da entrega #195

Os 14 critérios foram marcados como PASS na VPS no SHA implantado:

1. superadmin lista os dois tenants;
2. superadmin edita produtos dos dois tenants;
3. owner Hub2You abre produto publicado sem 404;
4. currículo, oferta, preview e publicação carregam;
5. owner é bloqueado no tenant alheio;
6. link gratuito autentica, retorna e libera biblioteca;
7. gratuito não cria checkout/cobrança;
8. pago exige checkout e não libera antes da confirmação;
9. aulas/PDFs só pela biblioteca;
10. comprador não edita/publica;
11. replay não duplica entitlement;
12. dados existentes preservados;
13. smoke no SHA real da VPS;
14. rollback disponível se qualquer gate falhar.

Detalhes e contagens preservadas estão no [registro da produção](PRODUCTION_CURRENT.md)
e no [Issue #195](https://github.com/autonom-ia/appsell/issues/195).

## Próximas ações, sem confundir com regressão

- Documentar e decidir staging separado, RPO/RTO e política de refund/chargeback.
- Configurar recebimentos/Stripe por tenant em slice próprio, sem alterar a
  regra de publicação nem liberar conteúdo antes do checkout confirmado.
- Toda mudança futura deve usar Issue → branch → PR → review → aprovação →
  merge → deploy com backup e rollback.

## Links

- [Estado atual de produção](PRODUCTION_CURRENT.md)
- [Progresso](PROGRESS.md)
- [Decisões](DECISIONS.md)
- [Arquitetura](architecture/README.md)
- [Runbook de produção](runbooks/PRODUCTION_RELEASE_APPSSELL.md)
- [Memória do projeto](memory/MEMORY_PROJECT.md)
- [Memória da sessão](memory/MEMORY_SESSION.md)
