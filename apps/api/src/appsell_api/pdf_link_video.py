"""O vídeo do PDF toca no navegador, qualquer que seja o container de origem.

## O que estava errado

Os vídeos que o vendedor referencia de dentro do PDF são `.mov` gravados no
QuickTime. O proxy os repassava com `content-type: video/quicktime` e
`nosniff`, e o Chrome recusa esse tipo: `canPlayType("video/quicktime")` devolve
string vazia. O aluno via "Não foi possível reproduzir este vídeo" e não tinha o
que fazer — o formato do arquivo não é problema dele.

Medido em 30/08/2026 no arquivo real do vendedor: o container é QuickTime
(`ftyp` brand `qt  `), mas **o conteúdo já é H.264 Main + AAC**, exatamente o
que todo navegador toca. Só o invólucro estava errado.

## O que este módulo faz

Prepara um derivado MP4 e o guarda no storage, uma vez por endereço de origem:

- codecs já apresentáveis → `-c copy`, troca só o container. Medido em 4 s para
  um arquivo de 5 MB, sem perda e sem recodificar.
- codecs que o navegador não toca (ProRes, HEVC, AV1 em container antigo) →
  recodifica para H.264/AAC. É caro, e por isso acontece em segundo plano.

O `+faststart` põe o índice no começo do arquivo, que é o que permite ao aluno
arrastar a barra sem baixar o vídeo inteiro.

## Onde ele NÃO confia

O endereço vem de dentro de um PDF que o vendedor enviou, e quem busca passa a
ser o servidor. Isso é uma requisição saindo da nossa rede a mando de um dado
de terceiro, então o alvo é conferido contra a mesma allowlist de origens que o
BFF já usa (`APPSELL_PDF_VIDEO_ALLOWED_ORIGINS`) antes de qualquer download.
Sem allowlist configurada, nada é buscado.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
import tempfile
import threading
import time
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

import anyio
import httpx
from anyio import to_thread

_MIB: Final = 1024 * 1024

#: Teto do arquivo de origem. Acima disto o preparo é recusado em vez de ocupar
#: a máquina por tempo indefinido com um arquivo que ninguém dimensionou.
MAX_SOURCE_BYTES: Final = 512 * _MIB
#: Teto do derivado gerado, pelo mesmo motivo.
MAX_DERIVATIVE_BYTES: Final = 512 * _MIB
#: Quantos redirecionamentos o download aceita. Cada um é revalidado.
MAX_REDIRECTS: Final = 3
#: Prazo total do download, em segundos.
#:
#: O timeout do cliente é por operação: uma origem que envia alguns bytes a
#: cada 59 segundos nunca o dispara e segura o único slot de preparo para
#: sempre. Este prazo é sobre o relógio, do começo ao fim.
DOWNLOAD_DEADLINE_SECONDS: Final = 15 * 60

TRANSCODE_TIMEOUT_SECONDS: Final = 60 * 60


DERIVATIVE_MEDIA_TYPE: Final = "video/mp4"
DERIVATIVE_PREFIX: Final = "derivatives/pdf-link-video"


class PdfLinkVideoRejected(RuntimeError):
    """A origem não pode virar um vídeo apresentável, e não adianta tentar de novo."""


class PdfLinkVideoUnavailable(RuntimeError):
    """O preparo falhou por uma condição passageira e pode ser repetido."""


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    byte_size: int
    sha256_hex: str
    recoded: bool
    validator: str


def allowed_video_origins() -> tuple[str, ...]:
    """As origens de onde um vídeo do PDF pode ser buscado.

    Espelha `APPSELL_PDF_VIDEO_ALLOWED_ORIGINS`, a mesma variável que o BFF já
    consulta. Duas listas independentes divergiriam, e a que decide agora é
    esta: é o servidor que faz a requisição.
    """
    raw = os.environ.get("APPSELL_PDF_VIDEO_ALLOWED_ORIGINS", "")
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())


def trusted_video_target(value: object) -> str | None:
    """O endereço, conferido antes de o servidor buscar qualquer coisa."""
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or not parts.hostname:
        return None
    if port is not None and port != 443:
        return None
    origins = allowed_video_origins()
    if not origins:
        return None
    origin = f"https://{parts.hostname}"
    if origin not in origins:
        return None
    # O fragmento sai. Ele nunca chega ao servidor HTTP — é do navegador — mas
    # entra na chave do derivado, e a chave é o registro do que já foi
    # preparado. Um PDF citando `video.mov#1` até `video.mov#4` fazia a
    # plataforma baixar o mesmo arquivo quatro vezes e guardar quatro cópias
    # idênticas, com quatro nomes. Sem clique nenhum, agora que o preparo começa
    # na abertura da aula.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


async def origin_validator(target_url: str) -> str:
    """O que a origem diz sobre a versão atual do arquivo.

    Uma pergunta, pelo mesmo caminho conferido do download: allowlist, endereço
    público e conexão no IP validado, com cada redirecionamento revalidado.

    Existe para saber se o derivado guardado ainda corresponde à origem. Sem
    isto, trocar o vídeo mantendo o mesmo endereço deixaria o aluno recebendo a
    versão antiga para sempre — e o vendedor sem nenhuma forma de corrigir.

    **Nunca lê o corpo.** A resposta é aberta em fluxo e fechada sem consumir:
    `Range` é um pedido, não uma garantia, e uma origem que o ignore devolveria
    centenas de megabytes para responder uma pergunta de metadado.

    Quem chama trata a falha como "não sei": a origem fora do ar não pode
    invalidar um vídeo que já está pronto.
    """
    seen = target_url
    metodo = "HEAD"
    # Trocar de método não é um salto: contá-lo como redirecionamento faria o
    # teto valer para outra coisa que não a corrente de endereços.
    redirecionamentos = 0
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(20.0, connect=10.0),
        trust_env=False,
    ) as client:
        while redirecionamentos <= MAX_REDIRECTS:
            trusted = trusted_video_target(seen)
            if trusted is None:
                raise PdfLinkVideoRejected("video target is not an allowed origin")
            host = urlsplit(trusted).hostname or ""
            address = await to_thread.run_sync(public_address, host, abandon_on_cancel=True)
            cabecalhos = {"host": host}
            if metodo == "GET":
                cabecalhos["range"] = "bytes=0-0"
            try:
                async with client.stream(
                    metodo,
                    _pinned(trusted, address),
                    headers=cabecalhos,
                    extensions={"sni_hostname": host},
                ) as response:
                    status_code = response.status_code
                    location = response.headers.get("location", "")
                    validator = _origin_validator_header(status_code, response.headers)
            except (httpx.HTTPError, OSError) as error:
                # Rede, TLS e tempo esgotado são falhas passageiras: quem chama
                # trata como "não sei" e serve o que já existe.
                raise PdfLinkVideoUnavailable("video origin did not answer") from error

            if status_code in (301, 302, 303, 307, 308):
                redirecionamentos += 1
                seen = str(httpx.URL(trusted).join(location))
                continue
            if status_code == 405 and metodo == "HEAD":
                # A origem atende `GET` e recusa `HEAD` — comum, e não diz nada
                # sobre o arquivo existir. Pergunta de novo pelo primeiro byte.
                metodo = "GET"
                continue
            if status_code in DEFINITIVE_ABSENCE:
                # A origem existe e disse que este arquivo não. Diferente de não
                # conseguir perguntar: aqui servir a cópia antiga manteria no ar
                # algo que foi retirado.
                raise PdfLinkVideoRejected("video origin says the file is gone")
            if status_code not in {200, 206}:
                # Sobrecarga, erro do servidor, autenticação — nada disso é a
                # origem dizendo que o arquivo sumiu.
                raise PdfLinkVideoUnavailable("video origin did not answer usefully")
            if not validator:
                # Sem nada comparável, dizer qualquer coisa marcaria o derivado
                # como velho para sempre e o remux se repetiria a cada acesso.
                # "Não sei" serve o que já existe, que é o certo.
                raise PdfLinkVideoUnavailable("video origin exposes no comparable version")
            return validator[:120]
    raise PdfLinkVideoRejected("video target redirects too many times")


_CONTENT_RANGE_TOTAL: Final = re.compile(r"^bytes \d+-\d+/(\d+)$")


def _origin_validator_header(status_code: int, headers: Any) -> str:
    """O que identifica a versão do arquivo, sem confundir trecho com arquivo.

    O caminho alternativo pergunta pelo primeiro byte, e aí `content-length` é
    `1` — não o tamanho do arquivo. Compará-lo com o tamanho gravado no download
    marcaria o derivado como velho toda vez, e o remux se repetiria para sempre.

    Por isso o status importa: numa resposta parcial o total só pode vir do
    `content-range`, e numa resposta inteira só do `content-length`. Fora disso
    a resposta é vazia, e quem chama serve o vídeo que já existe.
    """
    marcado = headers.get("etag") or headers.get("last-modified")
    if marcado:
        return str(marcado)
    faixa = str(headers.get("content-range") or "").strip()
    if status_code == 206:
        encontrado = _CONTENT_RANGE_TOTAL.match(faixa)
        total = encontrado.group(1) if encontrado else ""
        return total if total.strip("0") else ""
    if faixa:
        return ""
    tamanho = str(headers.get("content-length") or "").strip()
    return tamanho if tamanho.isdigit() and tamanho.strip("0") else ""


#: Os status em que a origem afirma que o arquivo não está mais lá.
#:
#: Só estes autorizam tirar do ar um derivado já preparado. Qualquer outra
#: resposta ruim é a origem com problema, não o arquivo tendo sido retirado.
DEFINITIVE_ABSENCE: Final = frozenset({404, 410})

#: Quantos preparos podem estar pendentes ao mesmo tempo.
#:
#: O semáforo serializa a execução, mas não a fila: um PDF pode citar 512
#: endereços, e cada um criaria uma tarefa viva. Recusar cedo é o que impede um
#: vendedor de enfileirar semanas de trabalho na frente dos outros.
MAX_PENDING_PREPARATIONS: Final = 8


def derivative_key(seller_organization_id: str, target_url: str) -> str:
    """A chave do derivado, por vendedor e endereço de origem.

    A existência do objeto é o registro: sem tabela, sem migration, e sem um
    segundo lugar para o estado divergir do storage.

    O vendedor entra na chave de propósito. Uma chave só do endereço faria dois
    produtos de donos diferentes que citam a mesma URL compartilharem o objeto —
    e o segundo receberia a cópia que o primeiro preparou.

    A versão não entra na chave: o objeto é sobrescrito quando a origem muda, e
    quem guarda de qual versão ele veio é o metadado. Assim o endereço tem
    sempre um derivado, e a origem fora do ar não apaga o que já existe.
    """
    digest = hashlib.sha256(f"{seller_organization_id}\n{target_url}".encode()).hexdigest()
    return f"{DERIVATIVE_PREFIX}/{digest[:2]}/{digest}.mp4"


def public_address(host: str) -> str:
    """O endereço público do host, ou recusa.

    Metadata de nuvem, laço local e redes internas ficam de fora, em IPv4 e
    IPv6. Sem isto a allowlist protegeria só contra o endereço óbvio: bastaria
    uma origem permitida apontar o DNS para dentro.

    Devolve o endereço para que a conexão seja feita **nele**, e não numa
    segunda resolução. Validar um endereço e conectar em outro é uma checagem
    que não garante nada: entre as duas resoluções o DNS pode ter mudado.
    """
    try:
        resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise PdfLinkVideoUnavailable("video host does not resolve") from error
    if not resolved:
        raise PdfLinkVideoUnavailable("video host does not resolve")
    for entry in resolved:
        address = ipaddress.ip_address(entry[4][0])
        if not address.is_global or address.is_multicast:
            raise PdfLinkVideoRejected("video host resolves to a non public address")
    return str(ipaddress.ip_address(resolved[0][4][0]))


def _pinned(url: str, address: str) -> str:
    """O mesmo endereço, apontando para o IP já validado."""
    parts = urlsplit(url)
    literal = f"[{address}]" if ":" in address else address
    return parts._replace(netloc=literal).geturl()


async def download_source(target_url: str, destination: Path) -> tuple[int, str]:
    """Baixa a origem uma vez, e é o único ponto que fala com a rede.

    Antes o endereço ia direto para o `ffmpeg`, que segue redirecionamento por
    conta própria: bastava uma origem permitida responder `302` para um endereço
    interno e a allowlist deixava de valer. Aqui cada salto é revalidado, e o
    que o `ffmpeg` recebe é um arquivo local.

    O limite de bytes é aplicado durante a leitura, não pelo que a origem
    declara — um `content-length` mentiroso não enche o disco.
    """
    seen = target_url
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(60.0, connect=10.0),
        trust_env=False,
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            trusted = trusted_video_target(seen)
            if trusted is None:
                raise PdfLinkVideoRejected("video target is not an allowed origin")
            host = urlsplit(trusted).hostname or ""
            # `getaddrinfo` bloqueia, e bloquear o laço impede o próprio escopo
            # de cancelamento de rodar: um resolvedor travado seguraria tudo.
            address = await to_thread.run_sync(public_address, host, abandon_on_cancel=True)
            # Conecta no endereço que acabou de ser validado, e apresenta o
            # nome do host para o TLS: o certificado continua sendo conferido
            # contra o nome, e o DNS não tem uma segunda chance de responder.
            async with client.stream(
                "GET",
                _pinned(trusted, address),
                headers={"host": host},
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    seen = str(httpx.URL(trusted).join(location))
                    continue
                if response.status_code != 200:
                    raise PdfLinkVideoRejected("video origin refused the download")
                written = 0
                sink = await anyio.Path(destination).open("wb")
                try:
                    async for chunk in response.aiter_bytes(_MIB):
                        written += len(chunk)
                        if written > MAX_SOURCE_BYTES:
                            raise PdfLinkVideoRejected("video exceeds the accepted source size")
                        await sink.write(chunk)
                finally:
                    await sink.aclose()
                if written < 1:
                    raise PdfLinkVideoRejected("video origin returned an empty body")
                # O validador da origem entra na chave do derivado: trocar o
                # arquivo mantendo o endereço passa a gerar um objeto novo, em
                # vez de o aluno continuar recebendo a versão antiga para sempre.
                validator = (
                    response.headers.get("etag")
                    or response.headers.get("last-modified")
                    or str(written)
                )
                return written, validator[:120]
    raise PdfLinkVideoRejected("video target redirects too many times")


async def prepare_video(
    target_url: str,
    workspace: Path,
    *,
    transcode_gate: AbstractAsyncContextManager[Any] | None = None,
) -> PreparedVideo:
    """Baixa a origem e devolve um MP4 que o navegador toca.

    O download acontece aqui, porque a API é quem tem rede. A conversão
    acontece no serviço isolado, porque o arquivo é do vendedor e um parser de
    mídia não tem o que fazer no processo que carrega as credenciais.

    `transcode_gate` é onde a fila de conversão acontece, quando quem chama tem
    uma. O serviço isolado atende um arquivo por vez e recusa quem chega junto —
    ele não enfileira. Esperar aqui custa nada; levar a recusa custa o vídeo.
    Quem chama decide a política; este módulo não conhece semáforo nenhum.
    """
    source = workspace / "source.bin"
    # Prazo absoluto sobre tudo — conexão, cabeçalhos e corpo. O timeout do
    # cliente é por operação, e uma origem que envia alguns bytes a cada 59 s
    # nunca o dispara: seguraria o único slot de preparo para sempre.
    with anyio.fail_after(DOWNLOAD_DEADLINE_SECONDS):
        size, validator = await download_source(target_url, source)
    # Em thread: o arquivo vai a 512 MiB, e lê-lo inteiro aqui pararia todas as
    # outras respostas da API enquanto o hash corre.
    source_digest = await to_thread.run_sync(lambda: _file_sha256(source))

    destination = workspace / "derivative.mp4"
    async with transcode_gate or nullcontext():
        recoded = await to_thread.run_sync(
            lambda: remux_in_sandbox(source, size, source_digest, destination)
        )
    byte_size = destination.stat().st_size
    prepared_sha = await to_thread.run_sync(lambda: _file_sha256(destination))
    return PreparedVideo(
        path=destination,
        byte_size=byte_size,
        sha256_hex=prepared_sha,
        recoded=recoded,
        validator=validator,
    )


def _file_sha256(path: Path) -> str:
    """O hash do arquivo, lido em pedaços. Feito para rodar fora do event loop."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sandbox_client() -> tuple[httpx.BaseTransport | None, str]:
    """Como se fala com o serviço isolado, espelhando `pdf_links`."""
    remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
    remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
    if remote_socket:
        return httpx.HTTPTransport(uds=remote_socket, retries=0), "http://pdf-sandbox"
    if remote_url:
        parsed = httpx.URL(remote_url)
        base = f"{parsed.scheme}://{parsed.host}"
        if parsed.port is not None:
            base += f":{parsed.port}"
        return None, base
    raise PdfLinkVideoUnavailable("isolated media service is not configured")


#: Quantas vezes insistir quando o serviço isolado não atende.
#:
#: Ele se recicla depois de cada vídeo — é assim que um arquivo ruim não
#: contamina o próximo. Enquanto reinicia, quem chega recebe conexão recusada.
#: Isso não é falha: é o serviço fazendo o que deve, e quem pede só precisa
#: esperar. Sem isto, preparar vários vídeos seguidos falhava um sim, um não.
SANDBOX_ATTEMPTS: Final = 4
SANDBOX_RETRY_SECONDS: Final = 3.0


def remux_in_sandbox(source: Path, byte_size: int, sha256_hex: str, destination: Path) -> bool:
    """Manda o arquivo ao serviço isolado e grava o MP4 que ele devolve."""
    for tentativa in range(SANDBOX_ATTEMPTS):
        try:
            return _remux_once(source, byte_size, sha256_hex, destination)
        except PdfLinkVideoUnavailable:
            if tentativa == SANDBOX_ATTEMPTS - 1:
                raise
            time.sleep(SANDBOX_RETRY_SECONDS)
    raise PdfLinkVideoUnavailable("isolated media service did not answer")


def _remux_once(source: Path, byte_size: int, sha256_hex: str, destination: Path) -> bool:
    transport, base_url = _sandbox_client()
    try:
        with (
            httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(TRANSCODE_TIMEOUT_SECONDS, connect=5.0),
                trust_env=False,
                follow_redirects=False,
            ) as client,
            source.open("rb") as body,
            client.stream(
                "POST",
                f"{base_url}/prepare-video",
                headers={
                    "content-type": "application/octet-stream",
                    "x-appsell-byte-size": str(byte_size),
                    "x-appsell-sha256": sha256_hex,
                },
                content=body,
            ) as response,
        ):
            if response.status_code in {413, 415, 422}:
                raise PdfLinkVideoRejected("isolated media service refused the source")
            if response.status_code != 200 or (response.headers.get("content-type") != "video/mp4"):
                raise PdfLinkVideoUnavailable("isolated media service is unavailable")
            recoded = response.headers.get("x-appsell-recoded") == "true"
            # O derivado chega em pedaços e vai direto para o disco: meio giga na
            # memória do sandbox e de novo na da API derrubaria os dois.
            written = 0
            with destination.open("wb") as sink:
                for chunk in response.iter_bytes(_MIB):
                    written += len(chunk)
                    if written > MAX_DERIVATIVE_BYTES:
                        raise PdfLinkVideoRejected("prepared video is outside the accepted size")
                    sink.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        raise PdfLinkVideoUnavailable("isolated media service request failed") from error
    if written < 1:
        raise PdfLinkVideoRejected("isolated media service returned an empty body")
    return bool(recoded)


class PreparationRegistry:
    """Quem já está preparando o quê, para não preparar duas vezes.

    Sem isto, cada acesso do aluno enquanto o vídeo não fica pronto dispara um
    `ffmpeg` novo — e o caso em que o preparo é lento é exatamente o caso em que
    o aluno insiste.
    """

    #: Por quanto tempo uma recusa vale.
    #:
    #: Sem prazo, o endereço fica queimado: o vendedor troca o arquivo por um
    #: bom e o aluno continua sem vídeo para sempre. Com prazo, o trabalho caro
    #: não se repete a cada sondagem e a correção ainda chega.
    REJECTION_SECONDS = 10 * 60

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[str] = set()
        self._rejected: dict[str, float] = {}

    def start(self, key: str) -> bool:
        with self._lock:
            if key in self._running or self._rejected_at(key):
                return False
            self._running.add(key)
            return True

    def finish(self, key: str) -> None:
        with self._lock:
            self._running.discard(key)

    def reject(self, key: str) -> None:
        """A origem não pode virar vídeo, e tentar de novo daria no mesmo.

        Sem isto cada sondagem do aluno — e são dezenas — recomeça o download e
        a conversão de algo que já se sabe que não vai dar certo.
        """
        with self._lock:
            self._rejected[key] = time.monotonic()

    def rejected(self, key: str) -> bool:
        with self._lock:
            return self._rejected_at(key)

    def _rejected_at(self, key: str) -> bool:
        marcado = self._rejected.get(key)
        if marcado is None:
            return False
        if time.monotonic() - marcado > self.REJECTION_SECONDS:
            del self._rejected[key]
            return False
        return True

    def running(self, key: str) -> bool:
        with self._lock:
            return key in self._running


def temporary_workspace() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="appsell-pdf-video-")
