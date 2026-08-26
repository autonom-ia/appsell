from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO

import httpx
from PIL import Image, UnidentifiedImageError

from .pdf_raster import MAX_RASTER_DIMENSION
from .pdf_verifier import MAX_PDF_BYTES, PDF_MIME, PdfRejected, verified_pdf_file

PDF_PAGE_ARCHIVE_MIME = "application/vnd.autonomia.pdf-pages-v1+tar"
# 300 e o teto do LEASE, nao da memoria. O pdftoppm roda uma pagina por
# processo, entao a memoria nao cresce com o documento; o que cresce e o
# tempo. Medido num playbook real de 83 paginas: 0,3 s por pagina. Assumindo
# a VPS tres vezes mais lenta e reservando metade dos 600 s de lease para
# download, antivirus e arquivamento, sobram ~300 s de rasterizacao.
MAX_PDF_PAGES = 300
MAX_PDF_PAGE_BYTES = 8 * 1024 * 1024
# Precisa acompanhar MAX_PDF_PAGES. Medido: 0,25 MiB por pagina num documento
# com muitas capturas de tela. 300 paginas dao ~75 MiB no caso tipico; 512 MiB
# cobre um documento tres vezes mais pesado sem virar teto ilimitado.
MAX_PDF_DERIVATIVE_BYTES = 512 * 1024 * 1024
MAX_PDF_PAGE_PIXELS = MAX_RASTER_DIMENSION**2
_ARCHIVE_OVERHEAD_BYTES = 2 * 1024 * 1024
MAX_PDF_PAGE_ARCHIVE_BYTES = MAX_PDF_DERIVATIVE_BYTES + MAX_PDF_BYTES + _ARCHIVE_OVERHEAD_BYTES
# Worst-case sandbox disk while constructing the attested TAR: source,
# normalized source, ready copy, raster pages and the archive itself.
MAX_PDF_JOB_TEMP_BYTES = MAX_PDF_PAGE_ARCHIVE_BYTES + MAX_PDF_DERIVATIVE_BYTES + (3 * MAX_PDF_BYTES)
_PDFTOPPM_TIMEOUT_SECONDS = 120
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PdfDerivativeRejected(ValueError):
    pass


class PdfDerivativeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfPage:
    page_number: int
    path: Path
    byte_size: int
    sha256_hex: str
    width: int
    height: int


@dataclass(frozen=True)
class PdfPageArchive:
    path: Path
    byte_size: int
    sha256_hex: str
    source_sha256_hex: str
    ready_pdf_path: Path
    ready_pdf_byte_size: int
    ready_pdf_sha256_hex: str
    page_count: int
    total_page_bytes: int
    pages: tuple[PdfPage, ...]


@contextmanager
def prepared_pdf_file(
    source_path: Path, *, isolated_service_boundary: bool = False
) -> Iterator[PdfPageArchive]:
    source_size, source_sha = _bounded_digest(source_path, maximum=MAX_PDF_BYTES)
    if isolated_service_boundary:
        direct_root = Path(tempfile.mkdtemp(prefix="appsell-prepared-pdf-direct-"))
        os.chmod(direct_root, 0o700)
        try:
            yield build_pdf_page_archive(
                source_path,
                expected_size=source_size,
                expected_sha256_hex=source_sha,
                job_directory=direct_root,
            )
        finally:
            shutil.rmtree(direct_root, ignore_errors=True)
        return
    remote_url = os.environ.get("APPSELL_API_PDF_SANDBOX_URL", "").strip()
    remote_socket = os.environ.get("APPSELL_API_PDF_SANDBOX_SOCKET", "").strip()
    transport: httpx.BaseTransport | None = None
    base_url = ""
    if remote_socket:
        transport = httpx.HTTPTransport(uds=remote_socket, retries=0)
        base_url = "http://pdf-sandbox"
    elif remote_url:
        parsed = httpx.URL(remote_url)
        base_url = f"{parsed.scheme}://{parsed.host}"
        if parsed.port is not None:
            base_url += f":{parsed.port}"
    else:
        raise PdfDerivativeUnavailable("isolated PDF derivative service is not configured")

    temporary_root: Path | None = None
    try:
        temporary_root = Path(tempfile.mkdtemp(prefix="appsell-prepared-pdf-"))
        os.chmod(temporary_root, 0o700)
        archive_path = temporary_root / "pages.tar"
        try:
            with (
                httpx.Client(
                    transport=transport,
                    timeout=httpx.Timeout(180.0, connect=3.0),
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                source_path.open("rb") as source,
                client.stream(
                    "POST",
                    f"{base_url}/rasterize",
                    headers={
                        "content-type": PDF_MIME,
                        "x-appsell-byte-size": str(source_size),
                        "x-appsell-sha256": source_sha,
                    },
                    content=source,
                ) as response,
            ):
                if response.status_code in {413, 415, 422}:
                    raise PdfDerivativeRejected("PDF derivative service rejected the document")
                if response.status_code != 200:
                    raise PdfDerivativeUnavailable("PDF derivative service is unavailable")
                if (
                    response.headers.get("content-type") != PDF_PAGE_ARCHIVE_MIME
                    or response.headers.get("x-appsell-worker-recycle") != "1"
                    or response.headers.get("x-appsell-source-sha256") != source_sha
                ):
                    raise PdfDerivativeUnavailable("PDF derivative service attestation is invalid")
                try:
                    expected_archive_size = int(response.headers["content-length"])
                    expected_archive_sha = response.headers["x-appsell-sha256"]
                    page_count = int(response.headers["x-appsell-page-count"])
                    total_page_bytes = int(response.headers["x-appsell-total-page-bytes"])
                except (KeyError, ValueError) as error:
                    raise PdfDerivativeUnavailable(
                        "PDF derivative service metadata is invalid"
                    ) from error
                if (
                    not 1 <= expected_archive_size <= MAX_PDF_PAGE_ARCHIVE_BYTES
                    or not _SHA256.fullmatch(expected_archive_sha)
                    or not 1 <= page_count <= MAX_PDF_PAGES
                    or not 1 <= total_page_bytes <= MAX_PDF_DERIVATIVE_BYTES
                ):
                    raise PdfDerivativeUnavailable("PDF derivative service metadata is invalid")
                received = 0
                digest = hashlib.sha256()
                with archive_path.open("xb") as destination:
                    os.chmod(archive_path, 0o600)
                    for chunk in response.iter_bytes(64 * 1024):
                        received += len(chunk)
                        if received > expected_archive_size:
                            raise PdfDerivativeUnavailable(
                                "PDF derivative archive exceeds its attested size"
                            )
                        digest.update(chunk)
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                if received != expected_archive_size or digest.hexdigest() != expected_archive_sha:
                    raise PdfDerivativeUnavailable("PDF derivative archive integrity mismatch")
        except PdfDerivativeRejected:
            raise
        except PdfDerivativeUnavailable:
            raise
        except (httpx.HTTPError, OSError) as error:
            raise PdfDerivativeUnavailable("PDF derivative service request failed") from error

        with archive_path.open("rb") as archive_source:
            with verified_page_archive(
                archive_source,
                expected_size=expected_archive_size,
                expected_sha256_hex=expected_archive_sha,
                temporary_directory=str(temporary_root),
            ) as prepared:
                if (
                    prepared.source_sha256_hex != source_sha
                    or prepared.page_count != page_count
                    or prepared.total_page_bytes != total_page_bytes
                ):
                    raise PdfDerivativeUnavailable("PDF derivative archive metadata drifted")
                yield prepared
    finally:
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)


def build_pdf_page_archive(
    source_path: Path,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    job_directory: Path,
) -> PdfPageArchive:
    if not job_directory.is_dir() or job_directory.is_symlink():
        raise PdfDerivativeUnavailable("PDF derivative job directory is invalid")
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        raise PdfDerivativeUnavailable("pdftoppm is unavailable")
    try:
        with (
            source_path.open("rb") as source,
            verified_pdf_file(
                source,
                expected_size=expected_size,
                expected_sha256_hex=expected_sha256_hex,
                declared_content_type=PDF_MIME,
                temporary_directory=str(job_directory),
                isolated_service_boundary=True,
            ) as verified,
        ):
            if verified.page_count > MAX_PDF_PAGES:
                raise PdfDerivativeRejected("PDF exceeds the protected viewer page limit")
            raster_directory = job_directory / "raster"
            raster_directory.mkdir(mode=0o700)
            output_prefix = raster_directory / "page"
            try:
                completed = subprocess.run(  # noqa: S603 - fixed binary and bounded arguments
                    [
                        pdftoppm,
                        "-png",
                        "-scale-to",
                        str(MAX_RASTER_DIMENSION),
                        str(verified.ready_path),
                        str(output_prefix),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=_PDFTOPPM_TIMEOUT_SECONDS,
                    close_fds=True,
                    env=_raster_environment(),
                )
            except subprocess.TimeoutExpired as error:
                raise PdfDerivativeRejected("PDF derivative rasterization timed out") from error
            except OSError as error:
                raise PdfDerivativeUnavailable("PDF rasterizer could not start") from error
            if completed.returncode != 0 or len(completed.stderr) > 4_096:
                raise PdfDerivativeRejected("PDF derivative rasterization failed")

            pages: list[PdfPage] = []
            total_bytes = 0
            for page_number in range(1, verified.page_count + 1):
                candidate = _pdftoppm_page_path(
                    output_prefix, page_number=page_number, page_count=verified.page_count
                )
                page = _sanitize_page(candidate, page_number, raster_directory)
                total_bytes += page.byte_size
                if total_bytes > MAX_PDF_DERIVATIVE_BYTES:
                    raise PdfDerivativeRejected("PDF derivatives exceed the total size limit")
                pages.append(page)
            unexpected = {
                child.name
                for child in raster_directory.iterdir()
                if child.name not in {page.path.name for page in pages}
            }
            if unexpected:
                raise PdfDerivativeRejected("PDF rasterizer returned unexpected files")
            archive_path = job_directory / "pdf-pages-v1.tar"
            ready_pdf_path = job_directory / "ready.pdf"
            shutil.copyfile(verified.ready_path, ready_pdf_path)
            os.chmod(ready_pdf_path, 0o600)
            manifest = _manifest_bytes(
                source_sha256_hex=verified.source_sha256_hex,
                ready_pdf_byte_size=verified.ready_byte_size,
                ready_pdf_sha256_hex=verified.ready_sha256_hex,
                pages=pages,
                total_page_bytes=total_bytes,
            )
            with tarfile.open(archive_path, mode="w", format=tarfile.PAX_FORMAT) as archive:
                _add_bytes(archive, "manifest.json", manifest)
                _add_file(archive, "ready.pdf", ready_pdf_path)
                for page in pages:
                    _add_file(archive, f"pages/{page.page_number:04d}.png", page.path)
            os.chmod(archive_path, 0o600)
            archive_size, archive_sha = _bounded_digest(
                archive_path, maximum=MAX_PDF_PAGE_ARCHIVE_BYTES
            )
            return PdfPageArchive(
                path=archive_path,
                byte_size=archive_size,
                sha256_hex=archive_sha,
                source_sha256_hex=verified.source_sha256_hex,
                ready_pdf_path=ready_pdf_path,
                ready_pdf_byte_size=verified.ready_byte_size,
                ready_pdf_sha256_hex=verified.ready_sha256_hex,
                page_count=verified.page_count,
                total_page_bytes=total_bytes,
                pages=tuple(pages),
            )
    except PdfDerivativeRejected:
        raise
    except PdfRejected as error:
        raise PdfDerivativeRejected("PDF verification failed before rasterization") from error


def _pdftoppm_page_path(output_prefix: Path, *, page_number: int, page_count: int) -> Path:
    number_width = len(str(page_count))
    return output_prefix.with_name(f"{output_prefix.name}-{page_number:0{number_width}d}.png")


@contextmanager
def verified_page_archive(
    source: BinaryIO,
    *,
    expected_size: int,
    expected_sha256_hex: str,
    temporary_directory: str | None = None,
) -> Iterator[PdfPageArchive]:
    if not 1 <= expected_size <= MAX_PDF_PAGE_ARCHIVE_BYTES:
        raise PdfDerivativeRejected("PDF page archive size is invalid")
    if not _SHA256.fullmatch(expected_sha256_hex):
        raise PdfDerivativeRejected("PDF page archive digest is invalid")
    root: Path | None = None
    archive_path: Path | None = None
    try:
        root = Path(tempfile.mkdtemp(prefix="appsell-pdf-pages-", dir=temporary_directory))
        os.chmod(root, 0o700)
        archive_path = root / "pages.tar"
        digest = hashlib.sha256()
        received = 0
        with archive_path.open("xb") as destination:
            os.chmod(archive_path, 0o600)
            while chunk := source.read(64 * 1024):
                received += len(chunk)
                if received > expected_size or received > MAX_PDF_PAGE_ARCHIVE_BYTES:
                    raise PdfDerivativeRejected("PDF page archive exceeds its declared size")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if received != expected_size or digest.hexdigest() != expected_sha256_hex:
            raise PdfDerivativeRejected("PDF page archive integrity mismatch")
        extracted = root / "verified"
        extracted.mkdir(mode=0o700)
        parsed = _parse_archive(archive_path, extracted)
        yield PdfPageArchive(
            path=archive_path,
            byte_size=received,
            sha256_hex=digest.hexdigest(),
            source_sha256_hex=parsed[0],
            ready_pdf_path=parsed[1],
            ready_pdf_byte_size=parsed[2],
            ready_pdf_sha256_hex=parsed[3],
            page_count=len(parsed[4]),
            total_page_bytes=sum(page.byte_size for page in parsed[4]),
            pages=parsed[4],
        )
    finally:
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)


def _parse_archive(
    path: Path, output_directory: Path
) -> tuple[str, Path, int, str, tuple[PdfPage, ...]]:
    try:
        with tarfile.open(path, mode="r:") as archive:
            members = archive.getmembers()
            if not members or len(members) > MAX_PDF_PAGES + 2:
                raise PdfDerivativeRejected("PDF page archive member count is invalid")
            names = [member.name for member in members]
            if names[:2] != ["manifest.json", "ready.pdf"] or any(
                not member.isfile() or member.issym() or member.islnk() or member.size < 1
                for member in members
            ):
                raise PdfDerivativeRejected("PDF page archive structure is invalid")
            if members[1].size > MAX_PDF_BYTES or any(
                member.size > MAX_PDF_PAGE_BYTES for member in members[2:]
            ):
                raise PdfDerivativeRejected("PDF page archive member exceeds its limit")
            manifest_member = members[0]
            if not manifest_member.isfile() or not 1 <= manifest_member.size <= 256 * 1024:
                raise PdfDerivativeRejected("PDF page manifest is invalid")
            manifest_source = archive.extractfile(manifest_member)
            if manifest_source is None:
                raise PdfDerivativeRejected("PDF page manifest is unavailable")
            manifest_bytes = manifest_source.read(256 * 1024 + 1)
            manifest = json.loads(manifest_bytes)
            if _canonical_json(manifest) != manifest_bytes:
                raise PdfDerivativeRejected("PDF page manifest is not canonical")
            source_sha = manifest.get("source_sha256")
            ready_pdf = manifest.get("ready_pdf")
            page_entries = manifest.get("pages")
            if (
                set(manifest)
                != {
                    "schema_version",
                    "source_sha256",
                    "ready_pdf",
                    "page_count",
                    "total_bytes",
                    "pages",
                }
                or manifest.get("schema_version") != 1
                or not isinstance(source_sha, str)
                or not _SHA256.fullmatch(source_sha)
                or not isinstance(ready_pdf, dict)
                or set(ready_pdf) != {"byte_size", "sha256"}
                or not isinstance(ready_pdf.get("byte_size"), int)
                or not 1 <= ready_pdf["byte_size"] <= MAX_PDF_BYTES
                or not isinstance(ready_pdf.get("sha256"), str)
                or not _SHA256.fullmatch(ready_pdf["sha256"])
                or not isinstance(page_entries, list)
                or not 1 <= len(page_entries) <= MAX_PDF_PAGES
                or manifest.get("page_count") != len(page_entries)
            ):
                raise PdfDerivativeRejected("PDF page manifest contract is invalid")
            ready_member = archive.getmember("ready.pdf")
            if ready_member.size != ready_pdf["byte_size"]:
                raise PdfDerivativeRejected("normalized PDF metadata does not match its bytes")
            ready_source = archive.extractfile(ready_member)
            if ready_source is None:
                raise PdfDerivativeRejected("normalized PDF is unavailable")
            ready_path = output_directory / "ready.pdf"
            ready_size, ready_sha = _copy_ready_pdf(
                ready_source,
                ready_path,
                int(ready_pdf["byte_size"]),
                str(ready_pdf["sha256"]),
            )
            pages: list[PdfPage] = []
            total = 0
            expected_names = ["manifest.json", "ready.pdf"]
            for page_number, entry in enumerate(page_entries, start=1):
                if not isinstance(entry, dict):
                    raise PdfDerivativeRejected("PDF page manifest entry is invalid")
                name = f"pages/{page_number:04d}.png"
                expected_names.append(name)
                member = archive.getmember(name)
                page_source = archive.extractfile(member)
                if page_source is None:
                    raise PdfDerivativeRejected("PDF page archive member is unavailable")
                destination = output_directory / f"page-{page_number}.png"
                page = _copy_and_validate_page(page_source, destination, entry, page_number)
                total += page.byte_size
                if total > MAX_PDF_DERIVATIVE_BYTES:
                    raise PdfDerivativeRejected("PDF page archive exceeds the derivative limit")
                pages.append(page)
            if names != expected_names or manifest.get("total_bytes") != total:
                raise PdfDerivativeRejected("PDF page archive is incomplete")
            return source_sha, ready_path, ready_size, ready_sha, tuple(pages)
    except PdfDerivativeRejected:
        raise
    except (KeyError, json.JSONDecodeError, OSError, tarfile.TarError, ValueError) as error:
        raise PdfDerivativeRejected("PDF page archive is invalid") from error


def _sanitize_page(source: Path, page_number: int, directory: Path) -> PdfPage:
    if not source.is_file() or source.is_symlink() or source.parent != directory:
        raise PdfDerivativeRejected("PDF rasterizer returned a missing page")
    destination = directory / f"verified-{page_number}.png"
    payload, width, height = _canonical_png(source)
    destination.write_bytes(payload)
    os.chmod(destination, 0o600)
    source.unlink()
    return PdfPage(
        page_number=page_number,
        path=destination,
        byte_size=len(payload),
        sha256_hex=hashlib.sha256(payload).hexdigest(),
        width=width,
        height=height,
    )


def _copy_and_validate_page(
    source: IO[bytes], destination: Path, entry: dict[str, object], page_number: int
) -> PdfPage:
    payload = source.read(MAX_PDF_PAGE_BYTES + 1)
    if len(payload) > MAX_PDF_PAGE_BYTES:
        raise PdfDerivativeRejected("PDF page exceeds the per-page limit")
    expected = {
        "page_number": page_number,
        "byte_size": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "width": entry.get("width"),
        "height": entry.get("height"),
    }
    if set(entry) != set(expected) or any(
        entry.get(key) != value for key, value in expected.items()
    ):
        raise PdfDerivativeRejected("PDF page metadata does not match its bytes")
    canonical, width, height = _canonical_png_bytes(payload)
    if canonical != payload or width != entry["width"] or height != entry["height"]:
        raise PdfDerivativeRejected("PDF page PNG is not canonical")
    destination.write_bytes(payload)
    os.chmod(destination, 0o600)
    return PdfPage(
        page_number=page_number,
        path=destination,
        byte_size=len(payload),
        sha256_hex=str(entry["sha256"]),
        width=width,
        height=height,
    )


def _copy_ready_pdf(
    source: IO[bytes],
    destination: Path,
    expected_size: int,
    expected_sha256_hex: str,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    received = 0
    first = b""
    tail = b""
    with destination.open("xb") as output:
        os.chmod(destination, 0o600)
        while chunk := source.read(64 * 1024):
            received += len(chunk)
            if received > expected_size or received > MAX_PDF_BYTES:
                raise PdfDerivativeRejected("normalized PDF exceeds its declared size")
            if not first:
                first = chunk[:8]
            tail = (tail + chunk)[-4_096:]
            digest.update(chunk)
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if (
        received != expected_size
        or digest.hexdigest() != expected_sha256_hex
        or not re.fullmatch(rb"%PDF-[12][.][0-9]", first)
        or re.search(rb"%%EOF[\x09\x0a\x0c\x0d\x20]*\Z", tail) is None
    ):
        raise PdfDerivativeRejected("normalized PDF integrity is invalid")
    return received, digest.hexdigest()


def _canonical_png(path: Path) -> tuple[bytes, int, int]:
    try:
        if not path.is_file() or path.is_symlink():
            raise PdfDerivativeRejected("PDF rasterizer output is not a regular file")
        if not 1 <= path.stat().st_size <= MAX_PDF_PAGE_BYTES:
            raise PdfDerivativeRejected("PDF rasterizer output exceeds the page limit")
        return _canonical_png_bytes(path.read_bytes())
    except OSError as error:
        raise PdfDerivativeRejected("PDF rasterizer output is unreadable") from error


def _canonical_png_bytes(payload: bytes) -> tuple[bytes, int, int]:
    if not 1 <= len(payload) <= MAX_PDF_PAGE_BYTES:
        raise PdfDerivativeRejected("PDF rasterizer output exceeds the page limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                if source.format != "PNG":
                    raise PdfDerivativeRejected("PDF rasterizer output is not PNG")
                width, height = int(source.width), int(source.height)
                if (
                    source.mode != "RGB"
                    or not 1 <= width <= MAX_RASTER_DIMENSION
                    or not 1 <= height <= MAX_RASTER_DIMENSION
                    or width * height > MAX_PDF_PAGE_PIXELS
                ):
                    raise PdfDerivativeRejected("PDF rasterizer output dimensions are invalid")
                # The archive crosses from the untrusted sandbox into the API. Never
                # decode pixels until the IHDR dimensions have passed our tighter cap.
                source.load()
                image = source.copy()
    except PdfDerivativeRejected:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
    ) as error:
        raise PdfDerivativeRejected("PDF rasterizer output is invalid") from error
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=6)
    canonical = output.getvalue()
    if len(canonical) > MAX_PDF_PAGE_BYTES:
        raise PdfDerivativeRejected("PDF rasterizer output exceeds the page limit")
    return canonical, width, height


def _manifest_bytes(
    *,
    source_sha256_hex: str,
    ready_pdf_byte_size: int,
    ready_pdf_sha256_hex: str,
    pages: list[PdfPage],
    total_page_bytes: int,
) -> bytes:
    return _canonical_json(
        {
            "schema_version": 1,
            "source_sha256": source_sha256_hex,
            "ready_pdf": {
                "byte_size": ready_pdf_byte_size,
                "sha256": ready_pdf_sha256_hex,
            },
            "page_count": len(pages),
            "total_bytes": total_page_bytes,
            "pages": [
                {
                    "page_number": page.page_number,
                    "byte_size": page.byte_size,
                    "sha256": page.sha256_hex,
                    "width": page.width,
                    "height": page.height,
                }
                for page in pages
            ],
        }
    )


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _add_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(payload)
    info.mode = 0o600
    info.mtime = 0
    info.uid = 10001
    info.gid = 10001
    info.uname = ""
    info.gname = ""
    archive.addfile(info, io.BytesIO(payload))


def _add_file(archive: tarfile.TarFile, name: str, path: Path) -> None:
    info = archive.gettarinfo(str(path), arcname=name)
    info.mode = 0o600
    info.mtime = 0
    info.uid = 10001
    info.gid = 10001
    info.uname = ""
    info.gname = ""
    with path.open("rb") as source:
        archive.addfile(info, source)


def _bounded_digest(path: Path, *, maximum: int) -> tuple[int, str]:
    size = path.stat().st_size
    if not 1 <= size <= maximum:
        raise PdfDerivativeRejected("PDF page archive exceeds the archive limit")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return size, digest.hexdigest()


def _raster_environment() -> dict[str, str]:
    allowed = ("PATH", "LANG", "LC_ALL", "TMPDIR")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment.update(
        {
            "HOME": "/nonexistent",
            "NO_PROXY": "*",
            "http_proxy": "",
            "https_proxy": "",
            "HTTP_PROXY": "",
            "HTTPS_PROXY": "",
        }
    )
    return environment
