"""O nome de quem entrou deixa de se perder no caminho.

O Identity conhece o nome da pessoa: o Cognito guarda `name`, `given_name` e
`family_name`, o client os declara legíveis, e o Appsell já pede o escopo
`profile`. O claim chega em todo login e é descartado ali mesmo — o BFF envia
ao `PUT /v1/me` apenas e-mail e verificação, e `identity_user` nunca teve
coluna para guardá-lo.

O efeito só ficou visível quando o rodapé do menu passou a mostrar dado real,
na #350: antes ele dizia "Sua conta" para todo mundo, um texto fixo no código,
e a ausência não aparecia. Substituir um placeholder por dado real não cria a
lacuna, revela a que já estava lá.

Guardar na sessão foi descartado: a identidade da sessão vive dentro do
token-vault, selada com criptografia autenticada e validação estrita. Mudar o
formato daquele envelope, com sessões ativas em produção, arrisca derrubar
todas elas — para exibir um nome.

A função nova convive com a antiga de propósito. A API só passa a chamar a de
seis parâmetros depois de implantada; manter a de cinco permite subir a
migration antes do serviço e voltar atrás sem quebrar o login. A remoção da
antiga fica para uma migration posterior, quando não houver mais chamador.
"""

from __future__ import annotations

from alembic import op

revision = "0048_nome_de_quem_entrou"
down_revision = "0047_cota_de_armazenamento"
branch_labels = None
depends_on = None

_ASSINATURA_NOVA = "app_private.sync_identity(uuid, text, text, text, boolean, text)"
_ASSINATURA_ANTIGA = "app_private.sync_identity(uuid, text, text, text, boolean)"

_READY_ATE_0047 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento'))"
)
_READY_ATE_0048 = (
    "'0043_material_role_by_media','0044_pdf_de_trezentas_paginas','0045_envio_em_partes',"
    "'0046_aula_de_meio_giga','0047_cota_de_armazenamento','0048_nome_de_quem_entrou'))"
)

_BASES_DO_READY: tuple[str, ...] = (
    "app_private.schema_ready_base_0016()",
    "app_private.schema_ready_base_0017()",
)

_REESCREVE = """
do $migration$
declare
  definicao text;
  reescrita text;
begin
  definicao := pg_get_functiondef('ALVO'::regprocedure);
  reescrita := replace(definicao, $de$DE$de$, $para$PARA$para$);
  if reescrita = definicao then
    raise exception 'ALVO não continha o texto esperado' using errcode='55000';
  end if;
  execute reescrita;
end $migration$;
"""

# O nome é dado da pessoa, não identificador: nunca participa de unicidade,
# de busca por igualdade nem de autorização. O limite alto acomoda nomes
# compostos reais; o controle de caracteres barra o que não é nome.
_COLUNA = """
alter table public.identity_user
  add column if not exists display_name text
  check (
    display_name is null
    or (display_name = btrim(display_name)
        and length(display_name) between 1 and 200
        and display_name !~ '[[:cntrl:]]')
  )
"""

_FUNCAO_NOVA = """
create function app_private.sync_identity(
  p_identity_user_id uuid,
  p_identity_issuer text,
  p_identity_sub text,
  p_email_normalized text,
  p_email_verified boolean,
  p_display_name text
) returns uuid
language plpgsql security definer
set search_path = pg_catalog, app_private, public
as $$
declare
  resolved_identity_id uuid;
  normalized_email text := lower(btrim(p_email_normalized));
  -- Vazio e só-espaços viram ausência: o Identity de alguns provedores devolve
  -- string vazia, e gravá-la faria o rodapé exibir um nome em branco no lugar
  -- do rótulo neutro.
  normalized_name text := nullif(btrim(coalesce(p_display_name, '')), '');
begin
  if app_private.context_text('capability') <> 'identity.sync'
     or app_private.context_uuid('identity_user_id') is distinct from p_identity_user_id
     or app_private.context_uuid('seller_organization_id') is not null then
    raise exception 'identity sync context is not authorized' using errcode = '28000';
  end if;
  if p_identity_user_id is null
     or p_identity_user_id is distinct from app_private.identity_user_id(
       p_identity_issuer, p_identity_sub
     )
     or p_identity_issuer is null or p_identity_issuer <> btrim(p_identity_issuer)
     or length(convert_to(p_identity_issuer, 'UTF8')) not between 3 and 255
     or p_identity_issuer ~ '[[:cntrl:]]'
     or p_identity_sub is null or p_identity_sub <> btrim(p_identity_sub)
     or length(convert_to(p_identity_sub, 'UTF8')) not between 3 and 255
     or p_identity_sub ~ '[[:cntrl:]]'
     or p_email_normalized is null or p_email_normalized <> normalized_email
     or length(normalized_email) not between 3 and 320
     or position('@' in normalized_email) <= 1
     or p_email_verified is null then
    raise exception 'invalid identity claims' using errcode = '22023';
  end if;
  -- Nome inválido não derruba o login: ele é acessório, e recusar a sessão
  -- inteira por causa dele trocaria um rótulo bonito por uma porta fechada.
  if normalized_name is not null
     and (length(normalized_name) > 200 or normalized_name ~ '[[:cntrl:]]') then
    normalized_name := null;
  end if;

  insert into public.identity_user (
    id, identity_issuer, identity_sub, email_normalized, email_verified, display_name
  ) values (
    p_identity_user_id, p_identity_issuer, p_identity_sub,
    normalized_email, p_email_verified, normalized_name
  )
  on conflict on constraint identity_user_issuer_sub_key do update set
    email_normalized = excluded.email_normalized,
    email_verified = excluded.email_verified,
    -- Só sobrescreve quando o Identity trouxe nome. Um login por um caminho
    -- que não emite o claim não pode apagar o nome já conhecido.
    display_name = coalesce(excluded.display_name, public.identity_user.display_name),
    updated_at = clock_timestamp()
  where public.identity_user.id = excluded.id
  returning id into resolved_identity_id;
  if resolved_identity_id is null then
    raise exception 'identity binding conflicts with existing user' using errcode = '28000';
  end if;
  return resolved_identity_id;
end
$$
"""


def upgrade() -> None:
    op.execute(_COLUNA)
    op.execute(_FUNCAO_NOVA)
    op.execute(f"alter function {_ASSINATURA_NOVA} owner to appsell_owner")
    op.execute(f"revoke all on function {_ASSINATURA_NOVA} from public")
    op.execute(f"grant execute on function {_ASSINATURA_NOVA} to appsell_runtime")
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0047)
            .replace("PARA", _READY_ATE_0048)
        )


def downgrade() -> None:
    # Descer apaga os nomes já coletados. É aceitável porque eles são
    # reconstituídos no login seguinte, a partir do mesmo claim do Identity —
    # nenhum dado nasce aqui.
    for alvo in _BASES_DO_READY:
        op.execute(
            _REESCREVE.replace("ALVO", alvo)
            .replace("DE", _READY_ATE_0048)
            .replace("PARA", _READY_ATE_0047)
        )
    op.execute(f"drop function if exists {_ASSINATURA_NOVA}")
    op.execute("alter table public.identity_user drop column if exists display_name")
