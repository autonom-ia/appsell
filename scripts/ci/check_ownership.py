#!/usr/bin/env python3
"""Posse de pastas por rótulo de trilha (#552).

Cada arquivo que o PR mexe tem, no máximo, uma trilha dona, lida de
`docs/programa/posse.json` (espelho de `TRILHAS.md`). Se o PR mexe em arquivo
de uma trilha, precisa do rótulo `trilha:<dona>`. Arquivo sem dono gera aviso,
não falha; exceções (testes, migrations, docs gerais, arquivos de uso comum)
não geram nada.

O rótulo roteia a revisão para a trilha dona. Não autoriza merge: isso
continua com o `ci-gate`, a revisão e as regras do AGENTS.md.

Os padrões são de shell (`fnmatch`), não regex: `pasta/` casa o prefixo,
`nome*` casa o glob, o resto casa o caminho exato. Vale o mais específico.
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


@dataclass(frozen=True)
class Rule:
    pattern: str
    label: str | None

    @property
    def weight(self) -> int:
        # Mais caracteres fixos = mais específico.
        return len(self.pattern.replace("*", ""))

    def matches(self, path: str) -> bool:
        if self.pattern.endswith("/"):
            return path.startswith(self.pattern)
        if "*" in self.pattern:
            return fnmatchcase(path, self.pattern)
        return path == self.pattern


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
        if base.startswith("_"):
            continue
        if not isinstance(base, str) or (base and not base.endswith("/")):
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
        if not isinstance(label, str) or not label.startswith(LABEL_PREFIX):
            raise ManifestError(f"rótulo {label!r} não começa com {LABEL_PREFIX}")
        for pattern in _names(label, bases):
            if pattern in seen and seen[pattern] != label:
                raise ManifestError(f"{pattern}: dono duplo ({seen[pattern]} e {label})")
            seen[pattern] = label
            rules.append(Rule(pattern, label))
    exceptions = raw.get("excecoes", {"": []})
    exception_rules = tuple(
        Rule(pattern, None) for pattern in (_names("excecoes", exceptions) if exceptions.get("") else [])
    )
    return Manifest(tuple(rules), exception_rules)


def check_pull_request(manifest: Manifest, files: list[str], labels: list[str]) -> Result:
    present = {label for label in labels if label.startswith(LABEL_PREFIX)}
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
    """Os caminhos entre crases de cada linha da tabela de posse, por rótulo."""
    tokens: dict[str, set[str]] = {}
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        quoted = line.split("`")[1::2]
        labels = [token for token in quoted if token.startswith(LABEL_PREFIX)]
        if len(labels) != 1:
            continue
        paths = {token for token in quoted if not token.startswith(LABEL_PREFIX)}
        tokens.setdefault(labels[0], set()).update(paths)
    return tokens


def _labels(raw: str) -> list[str]:
    try:
        labels = json.loads(raw)
    except json.JSONDecodeError as erro:
        raise ManifestError("rótulos não são uma lista JSON") from erro
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ManifestError("rótulos não são uma lista JSON de textos")
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--files", type=Path, required=True, help="um caminho por linha")
    parser.add_argument("--labels", required=True, help="lista JSON dos rótulos do PR")
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        labels = _labels(args.labels)
        files = [line.strip() for line in args.files.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError, ManifestError) as erro:
        print(f"::error::posse: não deu para checar ({erro})")
        return 1
    result = check_pull_request(manifest, files, labels)
    for path in result.warnings:
        print(f"::warning file={path}::posse: arquivo sem trilha dona; pergunte à Orquestração")
    for failure in result.failures:
        print(f"::error::posse: falta o rótulo da trilha dona ({failure})")
    if result.failures:
        return 1
    print(f"posse: {len(files)} arquivo(s), rótulos de trilha conferidos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
