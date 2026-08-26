"""O link externo volta a ser possível, e o vídeo para de aceitar lixo.

## O link nunca funcionou

`lesson_material_link_url_check` era:

    url ~ '^https://[^[:space:][:cntrl:]]{3,2000}$'

O PostgreSQL limita a contagem de repetição de uma regex POSIX a **255**. Com
`{3,2000}` ele nem chega a testar a URL: levanta `2201B — invalid regular
expression: invalid repetition count(s)` no momento do INSERT.

Ou seja, **nenhum link externo jamais entrou**. Não é caso de borda nem
configuração: a constraint nunca foi executável. E o erro chegava ao vendedor
como "Não foi possível falar com o servidor", que não sugere nada.

A intenção continua a mesma — https, sem espaço, sem controle, entre 11 e 2000
bytes — expressa de um jeito que o Postgres consegue executar: a regex cuida do
formato e `octet_length` cuida do tamanho.

## O vídeo aceitava o parâmetro errado

`video_id ~ '^[A-Za-z0-9_-]{1,64}$'` aceita quase tudo. Quem cola
`https://youtu.be/-vvGAKtV_Ek?si=LW2Oweh9T56VW1nR` e pega o pedaço errado grava
`LW2Oweh9T56VW1nR` — o parâmetro de rastreamento — e o embed nasce quebrado, sem
nenhum aviso.

Agora o formato depende do provedor:

- YouTube: exatamente 11 caracteres de `[A-Za-z0-9_-]`
- Vimeo: apenas dígitos, 6 a 12

Assim o `si=` de 16 caracteres é recusado na hora, e não vira um vídeo morto na
aula de alguém.

## Linhas que já existem

Uma linha com identificador fora do novo formato impediria a constraint de ser
criada. A migration arquiva essas linhas antes — elas já estavam quebradas na
prática, e arquivar preserva o registro em vez de apagar.

Por isso a regra vale para linhas **ativas**. Arquivada é histórico: nunca chega
ao aluno, e `embedSource` valida de novo na hora de montar o quadro. Exigir o
formato de uma linha que já saiu de circulação obrigaria a apagar registro para
poder criar a constraint.
"""

from __future__ import annotations

from alembic import op

revision = "0041_material_link_checks"
down_revision = "0040_asset_media_checks"
branch_labels = None
depends_on = None

# `{3,2000}` estoura o teto de 255 do POSIX. O formato fica na regex, o tamanho
# no octet_length — que e o que a API ja checava do lado dela.
_URL_CHECK = """
    alter table public.lesson_material_link
      drop constraint lesson_material_link_url_check;
    alter table public.lesson_material_link
      add constraint lesson_material_link_url_check
      check (
        url is null
        or (
          url ~ '^https://[^[:space:][:cntrl:]]+$'
          and octet_length(url) between 11 and 2000
          and url !~ '[<>"]'
        )
      );
"""

# Arquiva o que ja entrou com identificador invalido, e confere que sobrou zero.
#
# Suspender o FORCE e obrigatorio, nao precaucao. A tabela tem FORCE ROW LEVEL
# SECURITY, e nesse modo ate o dono e filtrado — `set row_security = off` nao
# ajuda, o proprio Postgres manda usar NO FORCE. Sem isto o UPDATE atinge ZERO
# linhas *em silencio* e a constraint seguinte falha por causa de uma linha que
# a migration acreditava ter corrigido. Foi o que aconteceu na primeira
# tentativa em producao.
#
# A contagem no fim existe porque um UPDATE que nao encontra nada nao e erro em
# SQL. Essa mudez foi o defeito; aqui ela vira excecao.
_ARCHIVE_INVALID = """
    do $migration$
    declare restantes bigint;
    begin
      -- Duas coisas, nao uma. `no force` isenta apenas o DONO da tabela, e o
      -- migrator e so membro do papel dono: sem virar `appsell_owner` a
      -- politica continua aplicando e o UPDATE volta a nao achar nada.
      set local role appsell_owner;
      alter table public.lesson_material_link no force row level security;

      update public.lesson_material_link
      set status = 'archived'
      where status = 'active'
        and video_id is not null
        and not (
          (provider = 'youtube' and video_id ~ '^[A-Za-z0-9_-]{11}$')
          or (provider = 'vimeo' and video_id ~ '^[0-9]{6,12}$')
        );

      select count(*) into restantes from public.lesson_material_link
      where status = 'active'
        and video_id is not null
        and not (
          (provider = 'youtube' and video_id ~ '^[A-Za-z0-9_-]{11}$')
          or (provider = 'vimeo' and video_id ~ '^[0-9]{6,12}$')
        );

      alter table public.lesson_material_link force row level security;
      reset role;

      if restantes > 0 then
        raise exception
          'still % material link(s) with an identifier the new check refuses', restantes
          using errcode='55000';
      end if;
    end $migration$;
"""

_VIDEO_CHECK = """
    alter table public.lesson_material_link
      drop constraint lesson_material_link_video_id_check;
    alter table public.lesson_material_link
      add constraint lesson_material_link_video_id_check
      check (
        status <> 'active'
        or video_id is null
        or (provider = 'youtube' and video_id ~ '^[A-Za-z0-9_-]{11}$')
        or (provider = 'vimeo' and video_id ~ '^[0-9]{6,12}$')
      );
"""

_URL_CHECK_0040 = """
    alter table public.lesson_material_link
      drop constraint lesson_material_link_url_check;
    alter table public.lesson_material_link
      add constraint lesson_material_link_url_check
      check (
        url is null
        or (url ~ '^https://[^[:space:][:cntrl:]]{3,255}$' and url !~ '[<>"]')
      );
"""

_VIDEO_CHECK_0040 = """
    alter table public.lesson_material_link
      drop constraint lesson_material_link_video_id_check;
    alter table public.lesson_material_link
      add constraint lesson_material_link_video_id_check
      check (video_id is null or video_id ~ '^[A-Za-z0-9_-]{1,64}$');
"""

_READY_TAIL = (
    "'0038_player_hides_materials','0039_asset_media_catalogue','0040_asset_media_checks')"
)
_READY_TAIL_WITH_0041 = _READY_TAIL[:-1] + ",'0041_material_link_checks')"


def _ready(old: str, new: str, label: str) -> str:
    return f"""
        do $migration$
        declare definition text; changed text;
        begin
          definition:=pg_get_functiondef('app_private.schema_ready_base_0016()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0016' using errcode='55000';
          end if;
          execute changed;
          definition:=pg_get_functiondef('app_private.schema_ready_base_0017()'::regprocedure);
          changed:=replace(definition,$old${old}$old$,$new${new}$new$);
          if changed=definition then
            raise exception '{label} 0017' using errcode='55000';
          end if;
          execute changed;
        end $migration$
        """


def upgrade() -> None:
    op.execute(_URL_CHECK)
    op.execute(_ARCHIVE_INVALID)
    op.execute(_VIDEO_CHECK)
    op.execute(_ready(_READY_TAIL, _READY_TAIL_WITH_0041, "cannot extend readiness for 0041 in"))


def downgrade() -> None:
    op.execute(_ready(_READY_TAIL_WITH_0041, _READY_TAIL, "cannot restore readiness from 0041 in"))
    op.execute(_VIDEO_CHECK_0040)
    # A regex original (`{3,2000}`) nao pode voltar: ela e invalida e faria
    # qualquer INSERT falhar. O downgrade devolve o mesmo formato dentro do teto
    # que o POSIX aceita.
    op.execute(_URL_CHECK_0040)
