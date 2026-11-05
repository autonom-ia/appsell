"""Consentimento, descadastro em um clique e supressão de e-mail opcional (#561).

A política de e-mail (docs/architecture/EMAIL_POLICY.md, Decisão 017) separa o
transacional, que não tem descadastro, do opcional, que só sai com
consentimento registrado e se descadastra em um clique. Até aqui não existia
onde registrar o consentimento, e por isso nenhum opcional sai. Esta migration
cria o lugar, e a regra que a fila aplica antes de qualquer envio.

Tudo é pelo **endereço** de e-mail normalizado, porque é para ele que se envia:

- `email_consent_text`: o texto exato que a pessoa aceitou, versionado e com o
  sha256 do corpo (LGPD art. 8º §2º: o ônus da prova do consentimento é nosso);
- `email_consent`: quem consentiu, com o quê, onde e quando. O escopo é a loja,
  ou a plataforma (`platform_news`), gravada como `_PLATAFORMA`. Trocar de
  e-mail não leva o consentimento ao endereço novo;
- `email_unsubscribe_token`: um token por e-mail opcional emitido. Só o sha256
  fica aqui; o token cru vai no payload do item da caixa de saída;
- `email_suppression` (por escopo) e `email_suppression_global` (todas as
  lojas: reclamação, hard bounce). Guardam o sha256 do endereço, não o
  endereço. Nenhuma tem FK para `identity_user`: a
  supressão sobrevive à exclusão da conta, porque respeitar o pedido de não
  receber é o que a exclusão não pode desfazer.

A ordem da checagem é global, escopo, consentimento. O transacional não passa
por nenhuma das três: `email_kind_is_transactional` lista os tipos, e um tipo
que não está nela é opcional (fail-closed).

`claim_email_outbox` é reescrita pelo padrão da 0048 (`pg_get_functiondef` +
âncora): antes de escolher candidatos, o item opcional sem permissão vai a
`dead_letter` com `suppressed` ou `no_consent`. Hoje a constraint de `kind` só
aceita os dois tipos transacionais, então nada muda no que sai.

As tabelas ficam em `app_private`, com RLS `enable` + `force`, política só para
o dono e `revoke all`. O acesso é por função `security definer`.
`email_suppression_global` e as linhas de escopo da plataforma são exceção
justificada ao PK por loja: o pedido de não receber nada vale em todas.

O `downgrade` existe, mas apaga consentimentos e supressões. Só depois de
exportá-los, porque perder uma supressão é voltar a mandar para quem pediu
para sair.

Revision ID: 0066_consentimento_de_email
Revises: 0065_dono_da_conta_payout
"""

from __future__ import annotations

from alembic import op

revision = "0066_consentimento_de_email"
down_revision = "0065_dono_da_conta_payout"
branch_labels = None
depends_on = None

CAPACIDADE_CONSENTIMENTO = "notification.email.consent"
# Escopo da plataforma: o uuid nulo, que nenhuma loja tem.
PLATAFORMA = "00000000-0000-0000-0000-000000000000"
TIPOS_TRANSACIONAIS = ("purchase_confirmed", "access_link_redeemed")
FINALIDADES_DA_LOJA = ("store_news", "content_reminder")
FINALIDADES_DA_PLATAFORMA = ("platform_news",)

_CLAIM = "app_private.claim_email_outbox(uuid,text,integer,integer)"

_READY_ATE_0065 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout'))"
)
_READY_ATE_0066 = (
    "'0057_banner_na_vitrine','0058_capa_na_biblioteca','0059_canal_financial_global',"
    "'0060_atualizacao_chega_ao_aluno','0061_o_atraso_alcanca_o_aluno',"
    "'0062_mapping_do_produto_novo','0063_conciliacao_do_acesso',"
    "'0064_vendas_ativadas_pela_loja','0065_dono_da_conta_payout',"
    "'0066_consentimento_de_email'))"
)
_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

_REESCREVE = """
do $migration$
declare definicao text; reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  if (length(definicao)-length(replace(definicao,$de$DE$de$,''))) / length($de$DE$de$) <> 1 then
    raise exception 'ALVO nao continha a ancora exatamente uma vez' using errcode='55000';
  end if;
  reescrita := replace(definicao,$de$DE$de$,$para$PARA$para$);
  execute reescrita;
end $migration$
"""

# Âncora no texto da 0049: o fim do dead-letter de lease vencido, logo antes da
# escolha de candidatos. Ocorre uma vez.
ANCORA_DO_CLAIM = """        and queued.attempt_count>=10;
      with candidates as (
"""
TRECHO_DO_CLAIM = """        and queued.attempt_count>=10;
      -- 0066 (#561): opcional sem permissão não chega ao worker. Transacional
      -- nunca entra aqui.
      update app_private.email_outbox queued set status='dead_letter',
        lease_owner=null,lease_expires_at=null,
        error_code=app_private.email_optional_block_reason(
          queued.seller_organization_id,queued.recipient_email,queued.payload->>'purpose'),
        updated_at=clock_timestamp()
      where queued.status in ('pending','retry')
        and not app_private.email_kind_is_transactional(queued.kind)
        and app_private.email_optional_block_reason(
          queued.seller_organization_id,queued.recipient_email,
          queued.payload->>'purpose') is not null;
      with candidates as (
"""

_EMAIL_CHECK = (
    "check (email_normalized = app_private.normalize_email(email_normalized) "
    "and position('@' in email_normalized) > 1 and octet_length(email_normalized) <= 254)"
)
_FINALIDADES = FINALIDADES_DA_LOJA + FINALIDADES_DA_PLATAFORMA
_LISTA_FINALIDADES = ",".join(f"'{finalidade}'" for finalidade in _FINALIDADES)
_LISTA_PLATAFORMA = ",".join(f"'{finalidade}'" for finalidade in FINALIDADES_DA_PLATAFORMA)
_LISTA_TRANSACIONAIS = ",".join(f"'{tipo}'" for tipo in TIPOS_TRANSACIONAIS)

TABELAS = (
    "email_consent_text",
    "email_consent",
    "email_unsubscribe_token",
    "email_suppression",
    "email_suppression_global",
)

# Uma só normalização, usada em todo caminho: trim + lower, nada além. A
# supressão guarda o sha256 do endereço normalizado, não o endereço, porque é
# ela que sobrevive à exclusão da conta.
NORMALIZACAO = (
    """
    create function app_private.normalize_email(p_email text)
    returns text language sql immutable strict
    set search_path=pg_catalog as $$ select lower(btrim(p_email)) $$
    """,
    """
    create function app_private.email_sha256(p_email text)
    returns text language sql immutable strict
    set search_path=pg_catalog,app_private,public as $$
      select encode(digest(convert_to(app_private.normalize_email(p_email),'UTF8'),'sha256'),'hex')
    $$
    """,
)

_CRIA_TABELAS_MOLDE = (
    """
    create table app_private.email_consent_text (
      purpose text not null check (purpose in (__FINALIDADES__)),
      version smallint not null check (version between 1 and 999),
      body text not null check (octet_length(body) between 20 and 2000),
      body_sha256 text not null check (octet_length(body_sha256) = 64),
      created_at timestamptz not null default clock_timestamp(),
      primary key (purpose,version)
    )
    """,
    """
    create table app_private.email_consent (
      scope_id uuid not null,
      email_normalized text not null __EMAIL_CHECK__,
      purpose text not null,
      status text not null check (status in ('granted','revoked')),
      source text not null check (source in ('checkout','account','storefront_form')),
      text_version smallint not null,
      text_sha256 text not null check (octet_length(text_sha256) = 64),
      identity_user_id uuid references public.identity_user(id) on delete set null,
      granted_at timestamptz not null,
      revoked_at timestamptz,
      revoked_source text check (
        revoked_source is null or revoked_source in ('unsubscribe_link','account')
      ),
      updated_at timestamptz not null default clock_timestamp(),
      primary key (scope_id,email_normalized,purpose),
      foreign key (purpose,text_version)
        references app_private.email_consent_text(purpose,version),
      check ((purpose in (__FINALIDADES_DA_PLATAFORMA__)) = (scope_id = '__PLATAFORMA__'::uuid)),
      check ((status = 'revoked') = (revoked_at is not null))
    )
    """,
    """
    create table app_private.email_unsubscribe_token (
      token_sha256 text primary key check (octet_length(token_sha256) = 64),
      scope_id uuid not null,
      email_normalized text not null __EMAIL_CHECK__,
      purpose text not null check (purpose in (__FINALIDADES__)),
      created_at timestamptz not null default clock_timestamp(),
      used_at timestamptz
    )
    """,
    """
    create table app_private.email_suppression (
      scope_id uuid not null,
      email_sha256 text not null check (octet_length(email_sha256) = 64),
      reason text not null check (reason in ('unsubscribed','manual')),
      created_at timestamptz not null default clock_timestamp(),
      primary key (scope_id,email_sha256)
    )
    """,
    """
    create table app_private.email_suppression_global (
      email_sha256 text primary key check (octet_length(email_sha256) = 64),
      reason text not null check (reason in ('hard_bounce','complaint','manual')),
      created_at timestamptz not null default clock_timestamp()
    )
    """,
)

# Texto v1 de cada finalidade. Mudar o texto é versão nova, nunca update.
TEXTOS_V1 = {
    "store_news": (
        "Quero receber por e-mail as novidades desta loja, como produtos e módulos novos. "
        "Posso parar quando quiser, pelo link de descadastro em cada e-mail."
    ),
    "content_reminder": (
        "Quero receber por e-mail lembretes do conteúdo que comecei e não terminei nesta "
        "loja. Posso parar quando quiser, pelo link de descadastro em cada e-mail."
    ),
    "platform_news": (
        "Quero receber por e-mail as novidades do Autonom.ia Sell. "
        "Posso parar quando quiser, pelo link de descadastro em cada e-mail."
    ),
}

_FUNCOES_MOLDE = (
    """
    create function app_private.email_kind_is_transactional(p_kind text)
    returns boolean language sql immutable
    set search_path=pg_catalog as $$
      select coalesce(p_kind in (__TRANSACIONAIS__), false)
    $$
    """,
    # Devolve o motivo do bloqueio, ou null quando o envio opcional é permitido.
    # Ordem: supressão global, supressão do escopo, consentimento.
    """
    create function app_private.email_optional_block_reason(
      p_seller uuid,p_email text,p_purpose text
    ) returns text language plpgsql stable security definer
    set search_path=pg_catalog,app_private,public as $$
    declare v_scope uuid; v_email text;
    begin
      if p_email is null or p_purpose is null
         or p_purpose not in (__FINALIDADES__) then
        return 'no_consent';
      end if;
      v_email := app_private.normalize_email(p_email);
      v_scope := case when p_purpose in (__FINALIDADES_DA_PLATAFORMA__)
        then '__PLATAFORMA__'::uuid else p_seller end;
      if v_scope is null then
        return 'no_consent';
      end if;
      if exists(select 1 from app_private.email_suppression_global global
                where global.email_sha256=app_private.email_sha256(v_email))
         or exists(select 1 from app_private.email_suppression scoped
                   where scoped.scope_id=v_scope
                     and scoped.email_sha256=app_private.email_sha256(v_email)) then
        return 'suppressed';
      end if;
      if not exists(select 1 from app_private.email_consent consent
                    where consent.scope_id=v_scope and consent.email_normalized=v_email
                      and consent.purpose=p_purpose and consent.status='granted') then
        return 'no_consent';
      end if;
      return null;
    end $$
    """,
    # Registro do consentimento pela própria pessoa, logada: o e-mail vem da
    # identidade do contexto, nunca do parâmetro. Reconsentir depois de um
    # descadastro é ação nova dela, e tira a supressão do escopo.
    """
    create function app_private.record_email_consent(
      p_seller uuid,p_purpose text,p_text_version smallint,p_source text,p_request uuid
    ) returns void language plpgsql security definer
    set search_path=pg_catalog,app_private,public as $$
    declare v_identity uuid; v_email text; v_scope uuid; v_sha text;
    begin
      v_identity := app_private.context_uuid('identity_user_id');
      if app_private.context_text('capability') is distinct from
           '__CAPACIDADE__'
         or v_identity is null or p_request is null
         or p_purpose is null or p_purpose not in (__FINALIDADES__)
         or p_source is null or p_source not in ('checkout','account','storefront_form')
         or (p_purpose in (__FINALIDADES_DA_PLATAFORMA__)) <> (p_seller is null)
         or (p_seller is not null and app_private.context_uuid('seller_organization_id')
               is distinct from p_seller) then
        raise exception 'email consent is not authorized' using errcode='28000';
      end if;
      select app_private.normalize_email(person.email_normalized) into v_email
        from public.identity_user person
        where person.id=v_identity;
      select consent_text.body_sha256 into v_sha from app_private.email_consent_text consent_text
        where consent_text.purpose=p_purpose and consent_text.version=p_text_version;
      if v_email is null or v_sha is null then
        raise exception 'email consent is not authorized' using errcode='28000';
      end if;
      v_scope := coalesce(p_seller,'__PLATAFORMA__'::uuid);
      insert into app_private.email_consent(scope_id,email_normalized,purpose,status,source,
        text_version,text_sha256,identity_user_id,granted_at)
      values (v_scope,v_email,p_purpose,'granted',p_source,p_text_version,v_sha,v_identity,
        clock_timestamp())
      on conflict (scope_id,email_normalized,purpose) do update set
        status='granted',source=excluded.source,text_version=excluded.text_version,
        text_sha256=excluded.text_sha256,identity_user_id=excluded.identity_user_id,
        granted_at=excluded.granted_at,revoked_at=null,revoked_source=null,
        updated_at=clock_timestamp();
      delete from app_private.email_suppression scoped
        where scoped.scope_id=v_scope and scoped.email_sha256=app_private.email_sha256(v_email)
          and scoped.reason='unsubscribed';
      insert into public.audit_event(seller_organization_id,actor_identity_user_id,
        action,target_type,target_id,result,request_id,metadata)
      select p_seller,v_identity,'notification.email_consent_granted','email_consent',
        v_identity,'succeeded',p_request,
        jsonb_build_object('purpose',p_purpose,'text_version',p_text_version,'source',p_source)
      where p_seller is not null;
    end $$
    """,
    # Emissão interna, para as funções que enfileiram e-mail opcional. Não é
    # concedida ao appsell_runtime. Devolve o token cru uma vez.
    """
    create function app_private.issue_email_unsubscribe_token(
      p_seller uuid,p_email text,p_purpose text
    ) returns text language plpgsql volatile security definer
    set search_path=pg_catalog,app_private,public as $$
    declare v_token text; v_scope uuid;
    begin
      if p_email is null or p_purpose is null or p_purpose not in (__FINALIDADES__)
         or (p_purpose in (__FINALIDADES_DA_PLATAFORMA__)) <> (p_seller is null) then
        raise exception 'unsubscribe token is not authorized' using errcode='28000';
      end if;
      v_scope := coalesce(p_seller,'__PLATAFORMA__'::uuid);
      v_token := rtrim(translate(encode(gen_random_bytes(32),'base64'),'+/','-_'),'=');
      insert into app_private.email_unsubscribe_token(token_sha256,scope_id,email_normalized,
        purpose)
      values (encode(digest(convert_to(v_token,'UTF8'),'sha256'),'hex'),v_scope,
        app_private.normalize_email(p_email),p_purpose);
      return v_token;
    end $$
    """,
    # Descadastro sem login: o token é a autorização. Idempotente. O boolean só
    # serve ao log da API; a resposta ao navegador é a mesma nos dois casos.
    """
    create function app_private.unsubscribe_email(p_token text)
    returns boolean language plpgsql volatile security definer
    set search_path=pg_catalog,app_private,public as $$
    declare v_row app_private.email_unsubscribe_token%rowtype;
    begin
      if p_token is null or octet_length(p_token) not between 16 and 128 then
        return false;
      end if;
      select * into v_row from app_private.email_unsubscribe_token unsubscribe
        where unsubscribe.token_sha256=encode(digest(convert_to(p_token,'UTF8'),'sha256'),'hex')
        for update;
      if not found then
        return false;
      end if;
      update app_private.email_unsubscribe_token unsubscribe set
        used_at=coalesce(unsubscribe.used_at,clock_timestamp())
        where unsubscribe.token_sha256=v_row.token_sha256;
      update app_private.email_consent consent set status='revoked',
        revoked_at=clock_timestamp(),revoked_source='unsubscribe_link',
        updated_at=clock_timestamp()
        where consent.scope_id=v_row.scope_id
          and consent.email_normalized=v_row.email_normalized
          and consent.status='granted';
      insert into app_private.email_suppression(scope_id,email_sha256,reason)
        values (v_row.scope_id,app_private.email_sha256(v_row.email_normalized),'unsubscribed')
        on conflict (scope_id,email_sha256) do nothing;
      return true;
    end $$
    """,
)

# Molde sem f-string, como na 0049: as listas entram por replace, e o ruff não
# vê SQL montado por interpolação (S608) onde só há constantes deste arquivo.
_VALORES = {
    "__EMAIL_CHECK__": _EMAIL_CHECK,
    "__FINALIDADES_DA_PLATAFORMA__": _LISTA_PLATAFORMA,
    "__FINALIDADES__": _LISTA_FINALIDADES,
    "__TRANSACIONAIS__": _LISTA_TRANSACIONAIS,
    "__PLATAFORMA__": PLATAFORMA,
    "__CAPACIDADE__": CAPACIDADE_CONSENTIMENTO,
}


def _preencher(molde: str) -> str:
    for marcador, valor in _VALORES.items():
        molde = molde.replace(marcador, valor)
    return molde


_CRIA_TABELAS = tuple(_preencher(molde) for molde in _CRIA_TABELAS_MOLDE)
_FUNCOES = tuple(_preencher(molde) for molde in _FUNCOES_MOLDE)

_ASSINATURAS_DO_RUNTIME = (
    "app_private.record_email_consent(uuid,text,smallint,text,uuid)",
    "app_private.unsubscribe_email(text)",
)
_ASSINATURAS_INTERNAS = (
    "app_private.normalize_email(text)",
    "app_private.email_sha256(text)",
    "app_private.email_kind_is_transactional(text)",
    "app_private.email_optional_block_reason(uuid,text,text)",
    "app_private.issue_email_unsubscribe_token(uuid,text,text)",
)


_SEMENTE = (
    "insert into app_private.email_consent_text(purpose,version,body,body_sha256) "
    "values ('__FINALIDADE__',1,'__CORPO__',"
    "encode(digest(convert_to('__CORPO__','UTF8'),'sha256'),'hex'))"
)


def _sementes() -> list[str]:
    return [
        _SEMENTE.replace("__FINALIDADE__", finalidade).replace(
            "__CORPO__", corpo.replace("'", "''")
        )
        for finalidade, corpo in TEXTOS_V1.items()
    ]


def _reescreve(alvo: str, de: str, para: str) -> str:
    return _REESCREVE.replace("ALVO", alvo).replace("DE", de).replace("PARA", para)


def upgrade() -> None:
    for sql in NORMALIZACAO:
        op.execute(sql)
    for sql in _CRIA_TABELAS:
        op.execute(sql)
    for tabela in TABELAS:
        nome = f"app_private.{tabela}"
        op.execute(f"alter table {nome} owner to appsell_owner")
        op.execute(f"alter table {nome} enable row level security")
        op.execute(f"alter table {nome} force row level security")
        op.execute(
            f"create policy {tabela}_owner_all on {nome} "
            "to appsell_owner using (true) with check (true)"
        )
        op.execute(f"revoke all on table {nome} from public,appsell_runtime")
    for sql in _sementes():
        op.execute(sql)
    for sql in _FUNCOES:
        op.execute(sql)
    for assinatura in _ASSINATURAS_DO_RUNTIME + _ASSINATURAS_INTERNAS:
        op.execute(f"alter function {assinatura} owner to appsell_owner")
        op.execute(f"revoke all on function {assinatura} from public")
    for assinatura in _ASSINATURAS_DO_RUNTIME:
        op.execute(f"grant execute on function {assinatura} to appsell_runtime")
    op.execute(_reescreve(_CLAIM, ANCORA_DO_CLAIM, TRECHO_DO_CLAIM))
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, _READY_ATE_0065, _READY_ATE_0066))


def downgrade() -> None:
    # Apaga consentimentos e supressões: exportar antes (docstring).
    for alvo in _BASES_DO_READY:
        op.execute(_reescreve(alvo, _READY_ATE_0066, _READY_ATE_0065))
    op.execute(_reescreve(_CLAIM, TRECHO_DO_CLAIM, ANCORA_DO_CLAIM))
    # Tabelas antes das funções: o check de e-mail depende de normalize_email.
    for tabela in reversed(TABELAS):
        op.execute(f"drop table if exists app_private.{tabela}")
    for assinatura in reversed(_ASSINATURAS_DO_RUNTIME + _ASSINATURAS_INTERNAS):
        op.execute(f"drop function if exists {assinatura}")
