#!/usr/bin/env python3
"""Posse de pastas por rótulo de trilha (#552).

Cada arquivo que o PR mexe tem, no máximo, uma trilha dona, lida de
`docs/programa/posse.json` (espelho de `TRILHAS.md`). Se o PR mexe em arquivo
de uma trilha, precisa do rótulo `trilha:<dona>`. Arquivo sem dono gera aviso,
não falha; exceções (testes, migrations, docs gerais, arquivos de uso comum)
não geram nada.

O rótulo roteia a revisão para a trilha dona. Não autoriza merge: isso
continua com o `ci-gate`, a revisão e as regras do AGENTS.md.

Padrões: `pasta/` casa o prefixo; um nome com `*` casa segmento a segmento
(`fnmatch` por segmento, sem regex: `*` não atravessa `/`, mas o padrão que
casa uma pasta pega o que há dentro dela); o resto casa o caminho exato. Caixa não importa. Vale o padrão mais específico.

A lista de arquivos chega separada por NUL (`git diff -z`): sem isso, o git
põe entre aspas os nomes não ASCII e eles escapariam do dono.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path

LABEL_PREFIX = "trilha:"
MANIFEST = Path("docs/programa/posse.json")


class ManifestError(RuntimeError):
    """O manifesto ou a entrada não dão para checar."""


def _fold(text: str) -> str:
    return text.casefold()


@dataclass(frozen=True)
class Rule:
    pattern: str
    label: str | None

    @property
    def weight(self) -> int:
        # Mais caracteres fixos = mais específico.
        return len(self.pattern.replace("*", ""))

    def matches(self, path: str) -> bool:
        pattern, target = _fold(self.pattern), _fold(path)
        if pattern.endswith("/"):
            return target.startswith(pattern)
        if "*" in pattern:
            # Segmento a segmento: `*` não atravessa `/`. Caminho mais longo
            # casa se o padrão casou uma pasta (`lib/checkout*` pega
            # `lib/checkout-flow/x.ts`).
            want, have = pattern.split("/"), target.split("/")
            return len(have) >= len(want) and all(fnmatchcase(h, w) for h, w in zip(have, want))
        return target == pattern


@dataclass(frozen=True)
class Manifest:
    rules: tuple[Rule, ...]
    exceptions: tuple[Rule, ...]

    def owners(self, path: str) -> set[str]:
        hits = [rule for rule in self.rules if rule.matches(path)]
        if not hits:
            return set()
        best = max(rule.weight for rule in hits)
        return {rule.label for rule in hits if rule.weight == best and rule.label}

    def owner(self, path: str) -> str | None:
        owners = self.owners(path)
        if len(owners) > 1:
            raise ManifestError(f"{path}: mais de uma trilha dona {sorted(owners)}")
        return next(iter(owners), None)

    def is_exception(self, path: str) -> bool:
        return any(rule.matches(path) for rule in self.exceptions)


@dataclass(frozen=True)
class Result:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _names(label: str, bases: object) -> list[str]:
    if not isinstance(bases, dict) or not bases:
        raise ManifestError(f"{label}: esperava um mapa de base para nomes")
    patterns: list[str] = []
    for base, names in bases.items():
        if not isinstance(base, str):
            raise ManifestError(f"{label}: base inválida {base!r}")
        if base.startswith("_"):
            # Só o comentário `_leia` das exceções é pulado; em trilha, base com
            # `_` esconderia nomes do dono.
            if label != "excecoes" or base != "_leia":
                raise ManifestError(f"{label}: base {base!r} começa com _")
            continue
        if base and not base.endswith("/"):
            raise ManifestError(f"{label}: base {base!r} precisa terminar em /")
        if not isinstance(names, list) or not names:
            raise ManifestError(f"{label}: base {base!r} sem nomes")
        for name in names:
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ManifestError(f"{label}: nome inválido {name!r}")
            patterns.append(base + name)
    return patterns


def load_manifest(raw: object) -> Manifest:
    if not isinstance(raw, dict) or not isinstance(raw.get("trilhas"), dict) or not raw["trilhas"]:
        raise ManifestError("manifesto sem 'trilhas'")
    rules: list[Rule] = []
    seen: dict[str, str] = {}
    for label, bases in raw["trilhas"].items():
        if not isinstance(label, str) or not label.startswith(LABEL_PREFIX) or label != _fold(label):
            raise ManifestError(f"rótulo {label!r} precisa ser {LABEL_PREFIX}<nome> em minúsculas")
        for pattern in _names(label, bases):
            key = _fold(pattern)
            if key in seen and seen[key] != label:
                raise ManifestError(f"{pattern}: dono duplo ({seen[key]} e {label})")
            seen[key] = label
            rules.append(Rule(pattern, label))
    exceptions = raw.get("excecoes")
    exception_rules = () if exceptions is None else tuple(Rule(p, None) for p in _names("excecoes", exceptions))
    return Manifest(tuple(rules), exception_rules)


def check_pull_request(manifest: Manifest, files: list[str], labels: list[str]) -> Result:
    present = {_fold(label.strip()) for label in labels}
    failures: list[str] = []
    warnings: list[str] = []
    for path in sorted(set(files)):
        owners = manifest.owners(path)
        if len(owners) > 1:
            failures.append(f"ambíguo {sorted(owners)}: {path}")
        elif owners:
            owner = next(iter(owners))
            if owner not in present:
                failures.append(f"{owner}: {path}")
        elif not manifest.is_exception(path):
            warnings.append(path)
    return Result(failures, warnings)


def trilhas_tokens(markdown: str) -> dict[str, set[str]]:
    """Os caminhos entre crases de cada linha da tabela de posse, por rótulo.

    Linha de tabela que cita caminho sem exatamente um rótulo é erro: senão a
    paridade com o manifesto passaria por cima dela em silêncio.
    """
    tokens: dict[str, set[str]] = {}
    # Só a tabela de posse, antes da primeira seção (`## Reserva de migrations`).
    for line in markdown.split("\n## ", 1)[0].splitlines():
        if not line.startswith("|"):
            continue
        quoted = line.split("`")[1::2]
        labels = [token for token in quoted if _fold(token).startswith(LABEL_PREFIX)]
        paths = {token for token in quoted if not _fold(token).startswith(LABEL_PREFIX)}
        if not quoted:
            continue
        if len(labels) != 1:
            raise ManifestError(f"linha da tabela sem um rótulo único: {line[:80]}")
        tokens.setdefault(labels[0], set()).update(paths)
    return tokens


def escape_message(text: str) -> str:
    """Escapa o texto de um comando de workflow (::error::<texto>)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def escape_property(text: str) -> str:
    """Escapa o valor de uma propriedade de comando de workflow (file=...)."""
    return escape_message(text).replace(":", "%3A").replace(",", "%2C")


def _labels(raw: str) -> list[str]:
    try:
        labels = json.loads(raw)
    except json.JSONDecodeError as erro:
        raise ManifestError("rótulos não são uma lista JSON") from erro
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ManifestError("rótulos não são uma lista JSON de textos")
    return labels


def _files(path: Path) -> list[str]:
    try:
        return [item.decode("utf-8") for item in path.read_bytes().split(b"\0") if item]
    except UnicodeDecodeError as erro:
        raise ManifestError("nome de arquivo que não é UTF-8") from erro


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--files", type=Path, required=True, help="caminhos separados por NUL (git diff -z)")
    parser.add_argument("--labels", required=True, help="lista JSON dos rótulos do PR")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        labels = _labels(args.labels)
        files = _files(args.files)
    except (OSError, json.JSONDecodeError, ManifestError) as erro:
        print(f"::error::{escape_message(f'posse: não deu para checar ({erro})')}")
        return 1
    result = check_pull_request(manifest, files, labels)
    for path in result.warnings:
        print(f"::warning file={escape_property(path)}::posse: arquivo sem trilha dona; pergunte à Orquestração")
    for failure in result.failures:
        print(f"::error::{escape_message(f'posse: falta o rótulo da trilha dona ({failure})')}")
    if result.failures:
        return 1
    print(f"posse: {len(files)} arquivo(s), rótulos de trilha conferidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
