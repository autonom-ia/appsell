from __future__ import annotations

import importlib.util
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# Sem `datetime.UTC` nem `with (...)`: o script também é testado em Python 3.9.
UTC = timezone.utc


ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "cadeia_travada", ROOT / "scripts" / "ops" / "cadeia_travada.py"
)
assert _SPEC is not None and _SPEC.loader is not None
cadeia = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cadeia
_SPEC.loader.exec_module(cadeia)

AGORA = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)
CADEIA_LIMPA = (
    "email_stalled|0|\nemail_dead_letter|0|\ngrant_without_email|0|\n"
    "week_sales|0|\nweek_grants|0|\nweek_emails_sent|0|\n"
)


def leituras(**quantidades: object) -> dict:
    """`nome=quantidade` ou `nome=(quantidade, instante do psql)`."""
    saida = {}
    for nome, valor in quantidades.items():
        quantidade, instante = valor if isinstance(valor, tuple) else (valor, None)
        saida[nome] = cadeia.Leitura(quantidade, instante)
    return saida


def ler_cadeia(saida: str) -> dict:
    return cadeia.ler_leituras(
        saida, permitidas=cadeia.METRICAS_DA_CADEIA, obrigatorias=cadeia.METRICAS_DA_CADEIA
    )


class LeituraTests(unittest.TestCase):
    def test_le_a_saida_do_psql(self) -> None:
        saida = "fulfillment_stalled|2|2026-11-05 11:40:00+00\napproved_without_fulfillment|1|\n\n"
        lido = cadeia.ler_leituras(saida, permitidas=cadeia.CATEGORIAS_DA_CONCILIACAO)
        self.assertEqual(lido["fulfillment_stalled"], cadeia.Leitura(2, "2026-11-05 11:40:00+00"))
        self.assertEqual(lido["approved_without_fulfillment"], cadeia.Leitura(1, None))

    def test_saida_quebrada_e_falha_da_leitura_nunca_zero(self) -> None:
        # Ler "zero" de uma saída quebrada seria o silêncio que a #550 proíbe.
        quebradas = {
            "quantidade que não é número": "email_stalled|ERRO|\n",
            "dígito que não é 0-9": "email_stalled|²|\n",
            "quantidade vazia": "email_stalled||\n",
            "linha sem colunas": "lixo\n",
            "categoria desconhecida": CADEIA_LIMPA + "outra_coisa|1|\n",
            "categoria repetida": CADEIA_LIMPA + "email_stalled|1|\n",
        }
        for caso, saida in quebradas.items():
            with self.subTest(caso), self.assertRaises(cadeia.LeituraFalhou):
                ler_cadeia(saida)

    def test_a_cadeia_vazia_ou_incompleta_e_falha(self) -> None:
        for saida in ("", "\n", "email_stalled|0|\n"):
            with self.subTest(saida=saida), self.assertRaises(cadeia.LeituraFalhou):
                ler_cadeia(saida)

    def test_a_conciliacao_vazia_e_zero(self) -> None:
        # Sem caso, a 0063 não devolve linha nenhuma: isso é zero de verdade.
        self.assertEqual(cadeia.ler_leituras("", permitidas=cadeia.CATEGORIAS_DA_CONCILIACAO), {})


class JanelaTests(unittest.TestCase):
    def test_recomeca_um_pouco_antes_do_fim_anterior(self) -> None:
        estado = {"fim": (AGORA - timedelta(minutes=5)).isoformat()}
        self.assertEqual(
            cadeia.inicio_da_janela(estado, agora=AGORA), AGORA - timedelta(minutes=10)
        )

    def test_estado_sem_fuso_usa_o_padrao(self) -> None:
        estado = {"fim": "2026-11-05T11:55:00"}
        self.assertEqual(
            cadeia.inicio_da_janela(estado, agora=AGORA), AGORA - timedelta(minutes=15)
        )

    def test_depois_de_muito_tempo_parado_olha_no_maximo_um_dia(self) -> None:
        estado = {"fim": (AGORA - timedelta(days=9)).isoformat()}
        self.assertEqual(cadeia.inicio_da_janela(estado, agora=AGORA), AGORA - timedelta(days=1))

    def test_sem_estado_ou_estado_no_futuro_usa_o_padrao(self) -> None:
        padrao = AGORA - timedelta(minutes=15)
        self.assertEqual(cadeia.inicio_da_janela({}, agora=AGORA), padrao)
        futuro = {"fim": (AGORA + timedelta(hours=1)).isoformat()}
        self.assertEqual(cadeia.inicio_da_janela(futuro, agora=AGORA), padrao)


class DecisaoTests(unittest.TestCase):
    def test_tudo_limpo_nao_alerta(self) -> None:
        decisao = cadeia.decidir(leituras(week_sales=0), {}, agora=AGORA)
        self.assertFalse(decisao.alertar)
        self.assertEqual(decisao.linhas, ())

    def test_problema_novo_alerta_com_o_texto_da_categoria(self) -> None:
        decisao = cadeia.decidir(leituras(grant_without_email=1), {}, agora=AGORA)
        self.assertTrue(decisao.alertar)
        self.assertEqual(decisao.linhas, ("acesso liberado sem e-mail há mais de 15 min: 1",))
        self.assertEqual(decisao.estado["ultimo_alerta"], AGORA.isoformat())

    def test_o_mesmo_problema_nao_repete_a_cada_5_minutos(self) -> None:
        primeira = cadeia.decidir(leituras(email_stalled=1), {}, agora=AGORA)
        segunda = cadeia.decidir(
            leituras(email_stalled=1), primeira.estado, agora=AGORA + timedelta(minutes=5)
        )
        self.assertFalse(segunda.alertar)

    def test_o_problema_que_continua_repete_uma_vez_por_dia(self) -> None:
        primeira = cadeia.decidir(leituras(email_stalled=1), {}, agora=AGORA)
        dia_seguinte = cadeia.decidir(
            leituras(email_stalled=1), primeira.estado, agora=AGORA + timedelta(hours=24)
        )
        self.assertTrue(dia_seguinte.alertar)

    def test_o_problema_que_cresce_alerta_na_hora(self) -> None:
        primeira = cadeia.decidir(leituras(fulfillment_dead_letter=1), {}, agora=AGORA)
        cresceu = cadeia.decidir(
            leituras(fulfillment_dead_letter=2), primeira.estado, agora=AGORA + timedelta(minutes=5)
        )
        self.assertTrue(cresceu.alertar)

    def test_um_sai_e_outro_entra_alerta_pelo_instante(self) -> None:
        # A contagem fica em 1, mas o caso é outro: o mais novo mudou.
        primeira = cadeia.decidir(
            leituras(grant_without_email=(1, "2026-11-05 11:40:00+00")), {}, agora=AGORA
        )
        outro = cadeia.decidir(
            leituras(grant_without_email=(1, "2026-11-05 11:44:00.5+00")),
            primeira.estado,
            agora=AGORA + timedelta(minutes=5),
        )
        self.assertTrue(outro.alertar)

    def test_o_mesmo_caso_com_o_mesmo_instante_nao_repete(self) -> None:
        primeira = cadeia.decidir(
            leituras(email_dead_letter=(1, "2026-11-05 11:40:00+00")), {}, agora=AGORA
        )
        igual = cadeia.decidir(
            leituras(email_dead_letter=(1, "2026-11-05 11:40:00+00")),
            primeira.estado,
            agora=AGORA + timedelta(minutes=5),
        )
        self.assertFalse(igual.alertar)

    def test_estado_com_tipo_errado_nao_quebra(self) -> None:
        estado = {"contagens": ["x"], "instantes": "y", "ultimo_alerta": 3}
        decisao = cadeia.decidir(leituras(email_stalled=1), estado, agora=AGORA)
        self.assertTrue(decisao.alertar)

    def test_categoria_fora_da_lista_nao_alerta(self) -> None:
        # `access_without_grant` só vira alerta depois de medida (0063).
        decisao = cadeia.decidir(leituras(access_without_grant=9, week_sales=0), {}, agora=AGORA)
        self.assertFalse(decisao.alertar)


class FalhaTests(unittest.TestCase):
    def test_a_checagem_que_falha_alerta_e_nao_avanca_a_janela(self) -> None:
        estado = {"fim": (AGORA - timedelta(minutes=5)).isoformat()}
        decisao = cadeia.decidir_falha(estado, agora=AGORA, motivo="docker saiu com 1")
        self.assertTrue(decisao.alertar)
        self.assertEqual(decisao.estado["fim"], estado["fim"])
        self.assertEqual(decisao.linhas, ("a checagem falhou: docker saiu com 1",))

    def test_a_falha_repete_no_maximo_uma_vez_por_hora(self) -> None:
        primeira = cadeia.decidir_falha({}, agora=AGORA, motivo="x")
        logo_depois = cadeia.decidir_falha(
            primeira.estado, agora=AGORA + timedelta(minutes=5), motivo="x"
        )
        uma_hora = cadeia.decidir_falha(
            primeira.estado, agora=AGORA + timedelta(hours=1), motivo="x"
        )
        self.assertFalse(logo_depois.alertar)
        self.assertTrue(uma_hora.alertar)


class ResumoTests(unittest.TestCase):
    def test_semana_sem_venda_diz_isso_no_resumo(self) -> None:
        linhas = cadeia.resumo_semanal(leituras(week_sales=0, week_grants=0, week_emails_sent=0))
        self.assertIn("nenhuma venda aprovada nos últimos 7 dias", linhas)

    def test_semana_com_venda_traz_as_contagens(self) -> None:
        linhas = cadeia.resumo_semanal(leituras(week_sales=3, week_grants=3, week_emails_sent=3))
        self.assertEqual(
            linhas, ("vendas aprovadas: 3", "acessos liberados: 3", "e-mails enviados: 3")
        )


class ConsultaTests(unittest.TestCase):
    def test_a_senha_vai_pelo_stdin_e_nunca_em_argumento(self) -> None:
        chamadas: list[tuple[list[str], str | None]] = []

        def rodar(argumentos: list[str], *, entrada: str | None) -> str:
            chamadas.append((argumentos, entrada))
            return "abc123\n" if argumentos[1] == "ps" else "email_stalled|0\n"

        with tempfile.TemporaryDirectory() as pasta:
            senha = Path(pasta) / "senha"
            senha.write_text("segredo-de-teste\n", encoding="utf-8")
            config = {
                "CADEIA_PG_SERVICO": "servico",
                "CADEIA_PG_BANCO": "banco",
                "CADEIA_SENHA_ARQUIVO": str(senha),
            }
            with mock.patch.object(cadeia, "_rodar", side_effect=rodar):
                cadeia.consultar("select 1", config)

        execucao = chamadas[-1]
        self.assertEqual(execucao[1], "segredo-de-teste\n")
        self.assertNotIn("segredo-de-teste", " ".join(execucao[0]))
        self.assertIn("user=appsell_reconciler", " ".join(execucao[0]))
        # Só leitura e com teto de tempo, sem depender da config do papel.
        self.assertIn("default_transaction_read_only=on", execucao[0][-1])
        self.assertIn("statement_timeout=", execucao[0][-1])

    def test_mais_de_um_conteiner_e_falha(self) -> None:
        config = {"CADEIA_PG_SERVICO": "s", "CADEIA_PG_BANCO": "b", "CADEIA_SENHA_ARQUIVO": "x"}
        with mock.patch.object(cadeia, "_rodar", return_value="a\nb\n"):
            with self.assertRaises(cadeia.LeituraFalhou):
                cadeia.consultar("select 1", config)


class EstadoTests(unittest.TestCase):
    def test_grava_inteiro_e_com_0600(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "estado" / "cadeia.json"
            cadeia.gravar_estado(caminho, {"fim": AGORA.isoformat()})
            self.assertEqual(cadeia.ler_estado(caminho), {"fim": AGORA.isoformat()})
            self.assertEqual(stat.S_IMODE(caminho.stat().st_mode), 0o600)
            self.assertEqual(sorted(os.listdir(caminho.parent)), ["cadeia.json"])

    def test_queda_no_meio_nao_troca_o_estado_antigo(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "cadeia.json"
            cadeia.gravar_estado(caminho, {"fim": "antigo"})
            with mock.patch.object(cadeia.json, "dump", side_effect=OSError("disco")):
                with self.assertRaises(OSError):
                    cadeia.gravar_estado(caminho, {"fim": "novo"})
            self.assertEqual(cadeia.ler_estado(caminho), {"fim": "antigo"})
            self.assertEqual(os.listdir(pasta), ["cadeia.json"])

    def test_estado_truncado_ou_com_tipo_errado_recomeca(self) -> None:
        with tempfile.TemporaryDirectory() as pasta:
            caminho = Path(pasta) / "cadeia.json"
            for conteudo in ('{"fim": "2026', "[1, 2]", "\xff"):
                caminho.write_text(conteudo, encoding="latin-1")
                with self.subTest(conteudo=conteudo):
                    self.assertEqual(cadeia.ler_estado(caminho), {})


class MainTests(unittest.TestCase):
    """`main()` de ponta a ponta, com o banco e o SMTP trocados por dublês."""

    def rodar(self, consultar: object) -> tuple[int, list, dict]:
        enviados: list = []
        with tempfile.TemporaryDirectory() as pasta:
            config = Path(pasta) / "alerta.env"
            config.write_text("x=1\n", encoding="utf-8")
            estado = Path(pasta) / "cadeia.json"
            valores = {
                "SMTP_USER_TRANSACIONAL": "a@b.c",
                "ALERTA_PARA": "d@e.f",
                "CADEIA_PG_SERVICO": "s",
                "CADEIA_PG_BANCO": "b",
                "CADEIA_SENHA_ARQUIVO": "x",
            }
            with mock.patch.object(cadeia, "ler_config", return_value=valores), mock.patch.object(
                cadeia, "validar_config"
            ), mock.patch.object(cadeia, "consultar", side_effect=consultar), mock.patch.object(
                cadeia, "enviar", side_effect=lambda email, _config: enviados.append(email)
            ):
                codigo = cadeia.main(["--config", str(config), "--estado", str(estado)])
            gravado = cadeia.ler_estado(estado)
        return codigo, enviados, gravado

    def test_porta_smtp_invalida_e_falha_de_envio_sem_traceback(self) -> None:
        config = {"SMTP_USER_TRANSACIONAL": "a@b.c", "ALERTA_PARA": "d@e.f"}
        with mock.patch.object(cadeia, "enviar", side_effect=ValueError("porta")), mock.patch("sys.stderr"):
            codigo = cadeia._entregar("t", ("l",), config, False)
        self.assertEqual(1, codigo)

    def test_tudo_limpo_nao_envia_e_grava_o_fim(self) -> None:
        respostas = iter(["", CADEIA_LIMPA])
        codigo, enviados, gravado = self.rodar(lambda _sql, _config: next(respostas))
        self.assertEqual((codigo, enviados), (0, []))
        self.assertIn("fim", gravado)

    def test_problema_novo_envia_o_alerta(self) -> None:
        cadeia_com_problema = CADEIA_LIMPA.replace(
            "grant_without_email|0|", "grant_without_email|1|2026-11-05 11:40:00+00"
        )
        respostas = iter(["", cadeia_com_problema])
        codigo, enviados, _ = self.rodar(lambda _sql, _config: next(respostas))
        self.assertEqual(codigo, 0)
        self.assertEqual(len(enviados), 1)
        self.assertIn("acesso liberado sem e-mail", enviados[0].get_content())

    def test_saida_quebrada_vira_alerta_de_falha(self) -> None:
        respostas = iter(["", "lixo\n"])
        codigo, enviados, gravado = self.rodar(lambda _sql, _config: next(respostas))
        self.assertEqual(codigo, 1)
        self.assertIn("a checagem falhou", enviados[0].get_content())
        self.assertNotIn("fim", gravado)

    def test_excecao_inesperada_vira_alerta_so_com_o_tipo(self) -> None:
        def explode(_sql: str, _config: dict) -> str:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "conteudo-secreto")

        codigo, enviados, _ = self.rodar(explode)
        self.assertEqual(codigo, 1)
        corpo = enviados[0].get_content()
        self.assertIn("a checagem falhou: UnicodeDecodeError", corpo)
        self.assertNotIn("conteudo-secreto", corpo)


class MensagemTests(unittest.TestCase):
    def test_o_email_leva_so_as_linhas_e_o_runbook(self) -> None:
        email = cadeia.mensagem(
            "venda travada", ("e-mail na fila há mais de 30 min: 2",), remetente="a@b.c", destino="d@e.f"
        )
        self.assertEqual(email["Subject"], "[Sell] venda travada")
        corpo = email.get_content()
        self.assertIn("e-mail na fila há mais de 30 min: 2", corpo)
        self.assertIn("docs/runbooks/cadeia-travada.md", corpo)


if __name__ == "__main__":
    unittest.main()
