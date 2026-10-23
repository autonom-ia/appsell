"""O plano do envio em partes, e as recusas que ele faz antes de assinar nada.

O envio direto de hoje é um `PUT` único com URL assinada de 300 s. Uma aula de
45 min em 1080p tem por volta de 800 MB, e numa linha de 10 Mbit/s isso leva
640 s: a URL vence antes, o cliente desiste aos 290 s, e o vendedor perde o
envio inteiro sem ter onde retomar.

Estes testes cobrem a parte pura do desenho. O contrato com o storage de
verdade está em `test_storage_s3_contract.py`, que roda contra o MinIO.
"""

from __future__ import annotations

import pytest

from appsell_api.asset_media import max_bytes as asset_max_bytes
from appsell_api.config import Settings
from appsell_api.storage_s3 import (
    MULTIPART_MAX_PARTS,
    MULTIPART_PART_BYTES,
    S3Storage,
    StorageError,
    UploadedPart,
    multipart_plan,
    needs_multipart,
)


class TestQuandoValeAPena:
    def test_arquivo_que_cabe_numa_parte_continua_no_put_unico(self) -> None:
        """Abaixo de uma parte, o caminho de hoje é melhor e continua valendo.

        Ele carrega o hash do arquivo inteiro na própria URL assinada, e o
        storage recusa no ato se os bytes não baterem. Trocar isso por partes
        não daria nada em troca.
        """
        assert needs_multipart(1) is False
        assert needs_multipart(MULTIPART_PART_BYTES) is False

    def test_acima_de_uma_parte_o_envio_e_em_partes(self) -> None:
        assert needs_multipart(MULTIPART_PART_BYTES + 1) is True


class TestPlano:
    def test_o_arquivo_e_dividido_sem_sobra_e_sem_falta(self) -> None:
        tamanho = MULTIPART_PART_BYTES * 3 + 1234
        plano = multipart_plan(tamanho)
        assert plano.part_count == 4
        assert plano.last_part_bytes == 1234
        assert sum(plano.bytes_for_part(n) for n in range(1, plano.part_count + 1)) == tamanho

    def test_um_multiplo_exato_nao_gera_parte_vazia(self) -> None:
        plano = multipart_plan(MULTIPART_PART_BYTES * 2)
        assert plano.part_count == 2
        assert plano.last_part_bytes == MULTIPART_PART_BYTES

    def test_uma_aula_de_45_minutos_cabe_em_partes_curtas(self) -> None:
        """O número que motivou o desenho, escrito para não se perder.

        800 MB numa linha de 10 Mbit/s levam 640 s no total. O que importa não é
        esse total, é que nenhuma requisição isolada precise durar tanto: cada
        parte leva por volta de 13 s, bem dentro da URL de 300 s.
        """
        plano = multipart_plan(800 * 1024 * 1024)
        assert plano.part_count == 50
        segundos_por_parte = MULTIPART_PART_BYTES * 8 / (10 * 1000 * 1000)
        assert segundos_por_parte < 30

    @pytest.mark.parametrize("tamanho", [0, -1])
    def test_tamanho_invalido_e_recusado(self, tamanho: int) -> None:
        with pytest.raises(StorageError, match="byte_size"):
            multipart_plan(tamanho)

    def test_um_arquivo_grande_demais_e_recusado_antes_de_assinar(self) -> None:
        with pytest.raises(StorageError, match="parts"):
            multipart_plan(MULTIPART_PART_BYTES * (MULTIPART_MAX_PARTS + 1))


class TestParteIndividual:
    def test_a_ultima_parte_tem_o_resto_e_as_outras_a_parte_inteira(self) -> None:
        plano = multipart_plan(MULTIPART_PART_BYTES * 2 + 10)
        assert plano.bytes_for_part(1) == MULTIPART_PART_BYTES
        assert plano.bytes_for_part(2) == MULTIPART_PART_BYTES
        assert plano.bytes_for_part(3) == 10

    @pytest.mark.parametrize("numero", [0, -1, 4])
    def test_parte_fora_do_plano_e_recusada(self, numero: int) -> None:
        """Assinar uma parte fora do plano deixaria o envio maior que o declarado."""
        plano = multipart_plan(MULTIPART_PART_BYTES * 2 + 10)
        with pytest.raises(StorageError, match="part_number"):
            plano.bytes_for_part(numero)


def _storage_local() -> S3Storage:
    """Um cliente que nunca fala com a rede.

    Assinar URL e recusar entrada inválida são operações locais. O contrato com
    o storage de verdade está em `test_storage_s3_contract.py`, contra o MinIO.
    """
    return S3Storage(
        Settings(
            environment="ci",
            storage_provider="s3",
            storage_internal_endpoint="http://storage.invalido:9000",
            storage_public_endpoint="http://storage.invalido:9000",
            storage_quarantine_bucket="appsell-quarantine",
            storage_ready_bucket="appsell-ready",
            storage_control_access_key="chave-de-teste",
            storage_control_secret_key="segredo-de-teste",  # noqa: S106 - valor de teste
            storage_allowed_origin="http://localhost:3000",
            malware_scanner_provider="deterministic",
            pdf_sandbox_url="http://pdf-sandbox.internal:8081/normalize",
        )
    )


class TestAssinaturaDaParte:
    def test_a_assinatura_prende_o_tamanho_e_o_hash_da_parte(self) -> None:
        """Sem o tamanho preso, uma parte poderia chegar com qualquer número de
        bytes e o objeto final passaria do declarado. Sem o hash preso, o
        storage aceitaria bytes alterados e a recusa só aconteceria no worker,
        depois do arquivo inteiro ter subido."""
        plano = multipart_plan(MULTIPART_PART_BYTES * 2 + 10)
        assinada = _storage_local().presign_upload_part(
            bucket="appsell-quarantine",
            key="tenants/x/quarantine/y",
            storage_upload_id="envio-1",
            part_number=1,
            plan=plano,
            sha256_hex="a" * 64,
        )
        assert assinada.required_content_length == MULTIPART_PART_BYTES
        assert "x-amz-checksum-sha256" in assinada.headers
        assert "partNumber=1" in assinada.url
        assert "uploadId=envio-1" in assinada.url

    def test_a_ultima_parte_e_assinada_com_o_resto(self) -> None:
        plano = multipart_plan(MULTIPART_PART_BYTES * 2 + 10)
        assinada = _storage_local().presign_upload_part(
            bucket="appsell-quarantine",
            key="tenants/x/quarantine/y",
            storage_upload_id="envio-1",
            part_number=3,
            plan=plano,
            sha256_hex="a" * 64,
        )
        assert assinada.required_content_length == 10

    def test_parte_fora_do_plano_nao_e_assinada(self) -> None:
        plano = multipart_plan(MULTIPART_PART_BYTES * 2)
        with pytest.raises(StorageError, match="part_number"):
            _storage_local().presign_upload_part(
                bucket="appsell-quarantine",
                key="tenants/x/quarantine/y",
                storage_upload_id="envio-1",
                part_number=3,
                plan=plano,
                sha256_hex="a" * 64,
            )


class TestFechamento:
    def test_um_conjunto_incompleto_nao_fecha_o_envio(self) -> None:
        """Fechar com parte faltando produziria um arquivo menor que o declarado,
        e ele existiria antes de alguém perceber."""
        plano = multipart_plan(MULTIPART_PART_BYTES * 2 + 10)
        with pytest.raises(StorageError, match="do not match"):
            _storage_local().complete_multipart_upload(
                bucket="appsell-quarantine",
                key="tenants/x/quarantine/y",
                storage_upload_id="envio-1",
                plan=plano,
                parts=(
                    UploadedPart(part_number=1, byte_size=MULTIPART_PART_BYTES, etag="a"),
                    UploadedPart(part_number=3, byte_size=10, etag="c"),
                ),
            )

    def test_uma_parte_com_tamanho_diferente_do_plano_nao_fecha(self) -> None:
        plano = multipart_plan(MULTIPART_PART_BYTES + 10)
        with pytest.raises(StorageError, match="do not match"):
            _storage_local().complete_multipart_upload(
                bucket="appsell-quarantine",
                key="tenants/x/quarantine/y",
                storage_upload_id="envio-1",
                plan=plano,
                parts=(
                    UploadedPart(part_number=1, byte_size=MULTIPART_PART_BYTES, etag="a"),
                    UploadedPart(part_number=2, byte_size=999, etag="b"),
                ),
            )


class TestAbertura:
    def test_arquivo_que_cabe_num_put_unico_nao_abre_envio_em_partes(self) -> None:
        with pytest.raises(StorageError, match="single request"):
            _storage_local().create_multipart_upload(
                bucket="appsell-quarantine",
                key="tenants/x/quarantine/y",
                content_type="video/mp4",
                byte_size=MULTIPART_PART_BYTES,
                upload_request_id="envio-1",
            )

    def test_acima_do_teto_do_catalogo_nao_abre_envio(self) -> None:
        """O teto por tipo continua sendo o do catálogo, e vale antes de assinar."""
        with pytest.raises(StorageError, match="byte_size"):
            _storage_local().create_multipart_upload(
                bucket="appsell-quarantine",
                key="tenants/x/quarantine/y",
                content_type="video/mp4",
                byte_size=asset_max_bytes("video/mp4") + 1,
                upload_request_id="envio-1",
            )


class TestListagemCortada:
    """Resposta cortada é resposta que não responde.

    `find_multipart_upload` existe para afirmar "há no máximo um envio aberto
    para esta chave", e é essa afirmação que autoriza fechar o envio. Se o
    storage devolve a listagem cortada, a afirmação não pode ser feita: devolver
    o primeiro que coube na página seria fechar o envio errado e montar o
    arquivo com as partes de outra tentativa.
    """

    class _StorageQueCorta:
        def list_multipart_uploads(self, **_kwargs: object) -> dict[str, object]:
            return {"Uploads": [{"UploadId": "um", "Key": "k"}], "IsTruncated": True}

    def test_uma_listagem_cortada_e_recusada_em_vez_de_adivinhada(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = _storage_local()
        monkeypatch.setattr(storage, "_control", self._StorageQueCorta())
        with pytest.raises(StorageError, match="truncated"):
            storage.find_multipart_upload(bucket="appsell-quarantine", key="k")

    def test_uma_listagem_inteira_continua_respondendo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Inteira:
            def list_multipart_uploads(self, **_kwargs: object) -> dict[str, object]:
                return {"Uploads": [{"UploadId": "um", "Key": "k"}], "IsTruncated": False}

        storage = _storage_local()
        monkeypatch.setattr(storage, "_control", _Inteira())
        assert storage.find_multipart_upload(bucket="appsell-quarantine", key="k") == "um"


class TestChecksumDasPartes:
    """O envio abre com `ChecksumAlgorithm=SHA256`, e o fechamento tem de dizer o
    checksum de cada parte (#460).

    Com o algoritmo declarado na abertura, o MinIO recusa o
    `CompleteMultipartUpload` cujas partes chegam só com o ETag. O checksum vem
    da listagem das partes, que é de onde o fechamento já tira o ETag.
    """

    class _Storage:
        def __init__(self) -> None:
            self.fechamento: dict[str, object] | None = None

        def list_parts(self, **_kwargs: object) -> dict[str, object]:
            return {
                "Parts": [
                    {
                        "PartNumber": 1,
                        "Size": MULTIPART_PART_BYTES,
                        "ETag": '"etag-1"',
                        "ChecksumSHA256": "c3VtYS0x",
                    },
                    {"PartNumber": 2, "Size": 10, "ETag": '"etag-2"', "ChecksumSHA256": "c3VtYS0y"},
                ],
                "IsTruncated": False,
            }

        def complete_multipart_upload(self, **kwargs: object) -> dict[str, object]:
            self.fechamento = kwargs
            return {"VersionId": "v1"}

    def test_a_listagem_guarda_o_checksum_de_cada_parte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage = _storage_local()
        monkeypatch.setattr(storage, "_control", self._Storage())

        partes = storage.list_multipart_parts(
            bucket="appsell-quarantine", key="k", storage_upload_id="envio"
        )

        assert [parte.checksum_sha256 for parte in partes] == ["c3VtYS0x", "c3VtYS0y"]

    def test_o_fechamento_manda_o_checksum_de_cada_parte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        falso = self._Storage()
        storage = _storage_local()
        monkeypatch.setattr(storage, "_control", falso)
        partes = storage.list_multipart_parts(
            bucket="appsell-quarantine", key="k", storage_upload_id="envio"
        )

        versao = storage.complete_multipart_upload(
            bucket="appsell-quarantine",
            key="k",
            storage_upload_id="envio",
            plan=multipart_plan(MULTIPART_PART_BYTES + 10),
            parts=partes,
        )

        assert versao == "v1"
        assert falso.fechamento is not None
        assert falso.fechamento["MultipartUpload"] == {
            "Parts": [
                {"PartNumber": 1, "ETag": '"etag-1"', "ChecksumSHA256": "c3VtYS0x"},
                {"PartNumber": 2, "ETag": '"etag-2"', "ChecksumSHA256": "c3VtYS0y"},
            ]
        }

    def test_parte_sem_checksum_na_listagem_fecha_so_com_o_etag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Quem decide se o checksum faltante é aceitável é o storage, não nós."""
        falso = self._Storage()
        storage = _storage_local()
        monkeypatch.setattr(storage, "_control", falso)

        storage.complete_multipart_upload(
            bucket="appsell-quarantine",
            key="k",
            storage_upload_id="envio",
            plan=multipart_plan(MULTIPART_PART_BYTES + 10),
            parts=(
                UploadedPart(part_number=1, byte_size=MULTIPART_PART_BYTES, etag="etag-1"),
                UploadedPart(part_number=2, byte_size=10, etag="etag-2"),
            ),
        )

        assert falso.fechamento is not None
        assert falso.fechamento["MultipartUpload"] == {
            "Parts": [
                {"PartNumber": 1, "ETag": '"etag-1"'},
                {"PartNumber": 2, "ETag": '"etag-2"'},
            ]
        }
