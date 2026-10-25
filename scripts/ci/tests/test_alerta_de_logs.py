import importlib.util
import json
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
_SPEC = importlib.util.spec_from_file_location(
    "alerta_de_logs", ROOT / "scripts" / "ops" / "alerta_de_logs.py"
)
assert _SPEC is not None and _SPEC.loader is not None
alerta = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = alerta
_SPEC.loader.exec_module(alerta)

INICIO = datetime(2026, 10, 23, 12, 0, 0, tzinfo=UTC)
FIM = datetime(2026, 10, 23, 12, 5, 0, tzinfo=UTC)


def linha(momento: str, corpo: dict[str, object] | str) -> str:
    texto = corpo if isinstance(corpo, str) else json.dumps(corpo)
    return f"{momento} {texto}"


class LeituraTests(unittest.TestCase):
    def test_conta_erro_da_api_por_event_e_do_web_por_marker(self) -> None:
        api = [
            linha("2026-10-23T12:01:00.123456789Z", {"level": "error", "event": "learning_domain_error_unmapped"}),
            linha("2026-10-23T12:02:00.000000001Z", {"level": "error", "event": "learning_domain_error_unmapped"}),
            linha("2026-10-23T12:03:00Z", {"level": "info", "event": "http_request"}),
        ]
        web = [
            linha("2026-10-23T12:04:00Z", {"level": "error", "marker": "appsell.stripe_webhook.dropped"}),
            linha("2026-10-23T12:04:30Z", "   ▲ Next.js 16.4.2"),
        ]

        resumo = alerta.resumir(
            {"sell-autonomia_api": api, "sell-autonomia_web": web}, inicio=INICIO, fim=FIM
        )

        self.assertEqual(
            {
                ("sell-autonomia_api", "learning_domain_error_unmapped"): 2,
                ("sell-autonomia_web", "appsell.stripe_webhook.dropped"): 1,
            },
            resumo.contagem,
        )
        self.assertEqual(3, len(resumo.amostras))

    def test_a_janela_nao_conta_duas_vezes_nem_pula_linha(self) -> None:
        # O cron roda a cada 5 min, e o `--since` do docker é inclusivo. A linha
        # no limite exato pertence à janela que começa nela, nunca às duas.
        api = [
            linha("2026-10-23T12:00:00Z", {"level": "error", "event": "no_inicio"}),
            linha("2026-10-23T12:05:00Z", {"level": "error", "event": "no_fim"}),
            linha("2026-10-23T11:59:59.999Z", {"level": "error", "event": "antes"}),
        ]

        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)

        self.assertEqual({("sell-autonomia_api", "no_inicio"): 1}, resumo.contagem)

    def test_critical_e_fatal_contam_como_erro(self) -> None:
        api = [
            linha("2026-10-23T12:01:00Z", {"level": "CRITICAL", "event": "a"}),
            linha("2026-10-23T12:01:00Z", {"level": "fatal", "event": "b"}),
            linha("2026-10-23T12:01:00Z", {"level": "warning", "event": "c"}),
        ]

        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)

        self.assertEqual({"a", "b"}, {chave[1] for chave in resumo.contagem})

    def test_erro_sem_event_nem_marker_aparece_com_nome_proprio(self) -> None:
        api = [linha("2026-10-23T12:01:00Z", {"level": "error"})]

        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)

        self.assertEqual({("sell-autonomia_api", "<sem event>"): 1}, resumo.contagem)


class AmostraTests(unittest.TestCase):
    def test_amostra_leva_so_campos_da_lista_e_nunca_pii_ou_segredo(self) -> None:
        segredo = "sk_live_" + "A1b2C3d4" * 6
        api = [
            linha(
                "2026-10-23T12:01:00Z",
                {
                    "level": "error",
                    "event": "falhou",
                    "request_id": "req-1",
                    "email": "maria@example.test",
                    "authorization": "Bearer abc",
                    "error_type": segredo,
                    # Texto livre: pode trazer e-mail ou caminho concreto.
                    "reason": "conta joao@example.test recusada",
                    "route": "/api/minha-conta/pedidos/ped_123",
                },
            )
        ]

        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)
        amostra = resumo.amostras[0]

        self.assertNotIn("maria@example.test", amostra)
        self.assertNotIn("joao@example.test", amostra)
        self.assertNotIn("ped_123", amostra)
        self.assertNotIn("reason", amostra)
        self.assertNotIn("route", amostra)
        self.assertNotIn("Bearer", amostra)
        self.assertNotIn(segredo, amostra)
        self.assertIn("<REDACTED>", amostra)
        self.assertIn("req-1", amostra)
        self.assertIn("falhou", amostra)

    def test_no_maximo_tres_amostras(self) -> None:
        api = [
            linha("2026-10-23T12:01:00Z", {"level": "error", "event": f"e{indice}"})
            for indice in range(10)
        ]

        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)

        self.assertEqual(3, len(resumo.amostras))
        self.assertEqual(10, sum(resumo.contagem.values()))


class MensagemTests(unittest.TestCase):
    def test_sem_erro_nao_ha_mensagem(self) -> None:
        resumo = alerta.resumir({"sell-autonomia_api": []}, inicio=INICIO, fim=FIM)

        self.assertIsNone(alerta.mensagem(resumo, remetente="a@x.test", destino="b@x.test"))

    def test_mensagem_traz_contagem_por_servico_e_por_event(self) -> None:
        api = [
            linha("2026-10-23T12:01:00Z", {"level": "error", "event": "x"}),
            linha("2026-10-23T12:02:00Z", {"level": "error", "event": "x"}),
        ]
        resumo = alerta.resumir({"sell-autonomia_api": api}, inicio=INICIO, fim=FIM)

        email = alerta.mensagem(resumo, remetente="a@x.test", destino="b@x.test")

        assert email is not None
        self.assertIn("2 erro", email["Subject"])
        corpo = email.get_content()
        self.assertIn("sell-autonomia_api", corpo)
        self.assertIn("x: 2", corpo)
        self.assertIn("2026-10-23T12:00:00", corpo)

    def test_falha_de_leitura_vira_alerta_e_nao_silencio(self) -> None:
        resumo = alerta.resumir(
            {"sell-autonomia_api": []},
            inicio=INICIO,
            fim=FIM,
            falhas={"sell-autonomia_web": "docker service logs saiu com 1"},
        )

        email = alerta.mensagem(resumo, remetente="a@x.test", destino="b@x.test")

        assert email is not None
        self.assertIn("leitura falhou", email.get_content())


class EstadoTests(unittest.TestCase):
    def test_a_proxima_janela_comeca_onde_a_anterior_terminou(self) -> None:
        anterior = {"fim": "2026-10-23T12:00:00+00:00"}

        inicio = alerta.inicio_da_janela(anterior, agora=FIM, janela_minutos=5)

        self.assertEqual(INICIO, inicio)

    def test_sem_estado_ou_com_estado_velho_olha_so_a_ultima_janela(self) -> None:
        # Depois de dias parado, mandar o acumulado de uma vez seria spam; o
        # buraco fica registrado no próprio e-mail pelo horário de início.
        self.assertEqual(INICIO, alerta.inicio_da_janela({}, agora=FIM, janela_minutos=5))
        velho = {"fim": "2026-10-20T00:00:00+00:00"}
        self.assertEqual(
            datetime(2026, 10, 23, 11, 5, tzinfo=UTC),
            alerta.inicio_da_janela(velho, agora=FIM, janela_minutos=5),
        )


class ConfigTests(unittest.TestCase):
    def test_le_chave_valor_sem_interpretar_aspas_nem_ecoar(self) -> None:
        texto = "# comentário\nSMTP_HOST_TRANSACIONAL=smtp.example.test\n\nALERTA_PARA = ops@example.test\n"

        config = alerta.ler_config(texto)

        self.assertEqual("smtp.example.test", config["SMTP_HOST_TRANSACIONAL"])
        self.assertEqual("ops@example.test", config["ALERTA_PARA"])

    def test_config_incompleta_diz_o_nome_que_falta(self) -> None:
        with self.assertRaises(alerta.ConfigInvalida) as erro:
            alerta.validar_config({"SMTP_HOST_TRANSACIONAL": "h"})

        self.assertIn("SMTP_USER_TRANSACIONAL", str(erro.exception))


if __name__ == "__main__":
    unittest.main()
