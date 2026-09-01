# Política de Cookies do Autonom.ia Sell

> Rascunho para revisão jurídica. Estrutura adaptada da Política de Cookies do
> google-saas. A lista de cookies foi extraída do código em `lib/cookies.ts`,
> não estimada.
>
> Última atualização: [preencher data da publicação]

## 1. O que esta política cobre

Explica os cookies e tecnologias parecidas usados no sell.autonomia.site, na área
do Vendedor e nas lojas públicas hospedadas na Plataforma.

## 2. Tecnologias essenciais que usamos

Todos os cookies abaixo são estritamente necessários para o serviço funcionar. Em
produção, todos usam o prefixo `__Host-`, o que os prende ao domínio, exige
conexão segura e impede que sejam lidos por subdomínios.

| Cookie | Para que serve |
|---|---|
| `appsell_session` | Guarda o identificador opaco da sua sessão. Sem ele, você não continua logado. |
| `appsell_oauth_tx` | Protege a ida e a volta do login, ligando o pedido à resposta do provedor de identidade. |
| `appsell_post_login` | Lembra para onde te levar depois que o login termina. |
| `appsell_oauth_recovery` | Evita que uma falha no login jogue o navegador em um laço infinito. Dura 2 minutos. |
| `appsell_csrf` e variantes por ação | Impedem que outro site envie ações em seu nome ao criar loja, criar produto, editar ou publicar. |

O identificador de sessão é opaco: ele não carrega seus dados nem token de
acesso. Os dados da sessão ficam no nosso servidor.

## 3. Cookies opcionais

Não usamos. O sell.autonomia.site não tem cookie de publicidade, de rede social
nem ferramenta de análise de terceiros.

Se isso mudar, atualizaremos esta política e pediremos o seu consentimento antes
de instalar qualquer cookie não essencial.

## 4. Como gerenciar sua escolha

Como só usamos cookies essenciais, não há banner de consentimento a exibir nem
preferências a ajustar.

Você pode bloquear ou apagar cookies pelas configurações do seu navegador. Se
bloquear os cookies acima, o login e a área do aluno deixam de funcionar.

## 5. Documentos relacionados

Política de Privacidade e Termos de Uso.
