# Auditoria de dependências e licenças

- Status: Scaffold auditado para vulnerabilities; licenças/SBOM ainda pendentes
- Data-base: 2026-07-10
- Natureza: controle de engenharia; não substitui parecer jurídico

## 1. Resultado atual

O scaffold Web/BFF agora possui `package.json` e `pnpm-lock.yaml` versionados.
Versões diretas atuais: Next.js 16.2.10, React/React DOM 19.2.7 e ioredis 5.10.0;
ferramentas incluem pnpm 10.20.0, TypeScript 5.9.3, Vitest 4.0.18 e Playwright
1.61.x. O App CI executou instalação congelada e `pnpm audit --prod` sem
vulnerabilidades conhecidas nos merges #11 e #15.

Esse verde é limitado a advisories do ecossistema npm no lockfile atual. Ainda
não existe inventário transitivo de licenças, notices, SBOM, imagem de container,
lock Python ou scan de sistema operacional. Portanto, licença/supply chain do
produto completo continuam não certificados.

## 2. Evidência herdada do discovery

O [Relatório de Verificação](../research/VERIFICATION_REPORT.md) registrou:

| Componente verificado | Evidência disponível | Limite |
|---|---|---|
| Identity SDK | testes/build observados | não certifica licença nem sessão segura no AppSell |
| Admin SDK | testes/build observados | backend Admin segue bloqueado para sellers/buyers |
| Financial SDK | testes/build observados | não prova scoping por operator |
| Financial | 147 testes/build; audit posterior separa 0 vulnerabilidades de produção e 16 findings dev/tooling | findings dev/tooling ainda precisam de triagem; não são usados para certificar o AppSell |
| Neuro.ai | testes/build observados | página/produtos não é base reutilizável do AppSell |
| Studio | design/runtime inspecionados | não há pacote UI completo aprovado para copiar |

Essas evidências orientam o risco, mas não são SBOM do AppSell. Código/dependência
de outro repositório só pode ser incorporado após confirmar proprietário,
licença, versão, provenance, manutenção e contrato de uso.

## 3. Política proposta de aceitação

| Categoria | Tratamento proposto |
|---|---|
| MIT, BSD-2/3-Clause, ISC, Apache-2.0 | permitida após preservar notices e checar patente/atribuição |
| MPL-2.0, EPL, LGPL | revisão jurídica/arquitetural antes de uso; registrar forma de link/modificação |
| GPL, AGPL, SSPL e source-available restritiva | bloqueada por padrão; somente com aprovação jurídica e executiva explícita |
| licença customizada/ausente/ambígua | bloqueada até esclarecimento escrito |
| assets, fontes, ícones, vídeo e templates | licença comercial e direitos de redistribuição precisam de evidência separada |
| SaaS/API externa | revisar ToS, DPA, região, retenção, suboperadores, exportação e encerramento |

Política final precisa ser aprovada pelo responsável jurídico/negócio. “Open
source” não é sinônimo de licença compatível.

## 4. Inventário obrigatório por PR

Para cada pacote/asset/serviço novo, registrar:

- nome, versão exata e ecossistema;
- uso runtime, build, test ou desenvolvimento;
- licença SPDX e fonte da licença;
- dependências transitivas e licenças;
- homepage/repositório e mantenedor;
- integridade/provenance do artefato;
- CVEs/advisories, severidade, alcance e exploitability;
- data da última release e sinais de manutenção;
- alternativa/justificativa e owner;
- notices/atribuições exigidos;
- dados enviados ao fornecedor e região, se serviço externo.

## 5. Baseline candidata a verificar

As escolhas abaixo vêm do ADR 0001, mas a coluna de licença permanece “verificar
na versão fixada”. Não é uma afirmação sobre a versão futura.

| Componente candidato | Uso | Verificação obrigatória |
|---|---|---|
| Node.js + pnpm + Turborepo | build/monorepo | versão LTS/estável, licença e provenance |
| Next.js + React | Web/BFF | licença da versão e transitivas runtime |
| Tailwind CSS | UI/build | licença da versão e plugins |
| Python 3.12 + FastAPI | API | licença e transitivas runtime |
| SQLAlchemy + driver PostgreSQL | persistência | licença do driver e compatibilidade async |
| PostgreSQL | banco | licença, extensões e imagem base |
| Redis | cache/coordenação | licença/distribuição exata da versão/imagem escolhida |
| SDK S3 | storage | licença, versão e fornecedor compatível |
| ferramentas de observabilidade | telemetria | licença, exportação de dados e custo |
| fontes/ícones derivados do Studio | visual | origem, licença e direito de redistribuição |

Redis exige atenção especial: nome/versão/imagem não bastam; a distribuição e a
licença exatas do artefato selecionado devem ser registradas antes de uso.

## 6. Pipeline obrigatório

### JavaScript/TypeScript

- instalação com lockfile congelado;
- auditoria de advisories separando runtime/dev sem esconder nenhum finding;
- inventário de licença transitiva em formato machine-readable;
- detecção de pacote sem licença, git dependency, URL/tarball e script de install;
- revisão de mudanças de lockfile e lifecycle scripts.

### Python

- lock com hashes;
- audit de vulnerabilities sobre versões resolvidas;
- inventário SPDX/licença transitiva;
- bloqueio de dependência por VCS/path não aprovada;
- verificação de wheels/source e imagem base.

### Container e assets

- SBOM CycloneDX ou SPDX por imagem/digest;
- scan de OS packages e secrets;
- imagem base fixada por digest e origem aprovada;
- notices agregados no artefato/release;
- asset ledger com autor, fonte, licença, transformação e escopo de uso.

## 7. Critério de vulnerabilidade

Severidade do scanner não é decisão automática. Cada finding registra:

- pacote e caminho transitivo;
- runtime/build/dev;
- código vulnerável alcançável ou não, com evidência;
- exposição e pré-condições;
- correção disponível e risco de regressão;
- mitigação temporária, owner e prazo.

Gate proposto:

- critical/high alcançável em runtime: bloqueia merge;
- critical/high sem alcance demonstrado: bloqueia até triagem independente;
- moderate/low: prazo/aceite de risco explícito conforme exposição;
- licença bloqueada/ausente: bloqueia merge, independentemente do scanner de CVE;
- ignore/allowlist exige motivo, owner, expiração e revisão.

## 8. Entregáveis ainda pendentes

- SBOM de dependências e imagem;
- relatório de licenças e notices;
- triagem completa de vulnerabilities além do gate npm do scaffold;
- lista de serviços externos/DPA;
- revisão independente de supply chain;
- atualização contínua deste documento quando versões ou superfícies mudarem.

Até esses itens existirem, somente o gate de vulnerabilities npm do scaffold
está verde; a aprovação jurídica de licenças e o gate completo de supply chain
continuam pendentes.
