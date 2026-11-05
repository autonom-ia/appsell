import importlib.util
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "cadeia_travada", ROOT / "scripts" / "ops" / "cadeia_travada.py"
)
assert _SPEC is not None and _SPEC.loader is not None
cadeia = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = cadeia
_SPEC.loader.exec_module(cadeia)

AGORA = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


class LeituraTests(unittest.TestCase):
    def test_le_a_saida_do_psql(self) -> None:
        saida = "fulfillment_stalled|2\nemail_dead_letter|0\n\n"
        self.assertEqual(
            cadeia.ler_contagens(saida), {"fulfillment_stalled": 2, "email_dead_letter": 0}
        )

    def test_quantidade_estranha_e_falha_da_leitura_nao_zero(self) -> None:
        # Ler "zero" de uma saída quebrada seria o silêncio que a #550 proíbe.
        with self.assertRaises(cadeia.LeituraFalhou):
            cadeia.ler_contagens("email_stalled|ERRO\n")


class JanelaTests(unittest.TestCase):
    def test_recomeca_um_pouco_antes_do_fim_anterior(self) -> None:
        estado = {"fim": (AGORA - timedelta(minutes=5)).isoformat()}
        self.assertEqual(
            cadeia.inicio_da_janela(estado, agora=AGORA), AGORA - timedelta(minutes=10)
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
        decisao = cadeia.decidir({"week_sales": 0}, {}, agora=AGORA)
        self.assertFalse(decisao.alertar)
        self.assertEqual(decisao.linhas, ())

    def test_problema_novo_alerta_com_o_texto_da_categoria(self) -> None:
        decisao = cadeia.decidir({"grant_without_email": 1}, {}, agora=AGORA)
        self.assertTrue(decisao.alertar)
        self.assertEqual(decisao.linhas, ("acesso liberado sem e-mail há mais de 15 min: 1",))
        self.assertEqual(decisao.estado["ultimo_alerta"], AGORA.isoformat())

    def test_o_mesmo_problema_nao_repete_a_cada_5_minutos(self) -> None:
        primeira = cadeia.decidir({"email_stalled": 1}, {}, agora=AGORA)
        segunda = cadeia.decidir(
            {"email_stalled": 1}, primeira.estado, agora=AGORA + timedelta(minutes=5)
        )
        self.assertFalse(segunda.alertar)

    def test_o_problema_que_continua_repete_uma_vez_por_dia(self) -> None:
        primeira = cadeia.decidir({"email_stalled": 1}, {}, agora=AGORA)
        dia_seguinte = cadeia.decidir(
            {"email_stalled": 1}, primeira.estado, agora=AGORA + timedelta(hours=24)
        )
        self.assertTrue(dia_seguinte.alertar)

    def test_o_problema_que_cresce_alerta_na_hora(self) -> None:
        primeira = cadeia.decidir({"fulfillment_dead_letter": 1}, {}, agora=AGORA)
        cresceu = cadeia.decidir(
            {"fulfillment_dead_letter": 2}, primeira.estado, agora=AGORA + timedelta(minutes=5)
        )
        self.assertTrue(cresceu.alertar)

    def test_categoria_fora_da_lista_nao_alerta(self) -> None:
        # `access_without_grant` só vira alerta depois de medida (0063).
        decisao = cadeia.decidir({"access_without_grant": 9, "week_sales": 0}, {}, agora=AGORA)
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
        linhas = cadeia.resumo_semanal({"week_sales": 0, "week_grants": 0, "week_emails_sent": 0})
        self.assertIn("nenhuma venda aprovada nos últimos 7 dias", linhas)

    def test_semana_com_venda_traz_as_contagens(self) -> None:
        linhas = cadeia.resumo_semanal({"week_sales": 3, "week_grants": 3, "week_emails_sent": 3})
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

    def test_mais_de_um_conteiner_e_falha(self) -> None:
        config = {"CADEIA_PG_SERVICO": "s", "CADEIA_PG_BANCO": "b", "CADEIA_SENHA_ARQUIVO": "x"}
        with (
            mock.patch.object(cadeia, "_rodar", return_value="a\nb\n"),
            self.assertRaises(cadeia.LeituraFalhou),
        ):
            cadeia.consultar("select 1", config)


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
