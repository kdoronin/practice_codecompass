from __future__ import annotations

import ast
from pathlib import Path
from typing import Callable, Optional


DEFAULT_EXCLUDED_DIRNAMES: tuple[str, ...] = (
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "site-packages",
    "node_modules",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
)


def _excluded_dirnames(ignored_dirnames: tuple[str, ...] | list[str] | set[str] | None) -> set[str]:
    if ignored_dirnames is None:
        return set(DEFAULT_EXCLUDED_DIRNAMES)
    return {item for item in ignored_dirnames if item}


def _is_ignored_path(path: Path, repo_root: Path, ignored_dirnames: set[str]) -> bool:
    try:
        rel_parts = path.relative_to(repo_root).parts
    except ValueError:
        return True
    return any(part in ignored_dirnames for part in rel_parts)


def find_python_files(
    repo_root: Path,
    ignored_dirnames: tuple[str, ...] | list[str] | set[str] | None = None,
) -> list[Path]:
    ignored = _excluded_dirnames(ignored_dirnames)
    files = [path for path in repo_root.rglob("*.py") if not _is_ignored_path(path, repo_root, ignored)]
    return sorted(files)


def resolve_import_to_path(
    module: str,
    repo_root: Path,
    ignored_dirnames: tuple[str, ...] | list[str] | set[str] | None = None,
) -> Optional[str]:
    ignored = _excluded_dirnames(ignored_dirnames)
    parts = module.split(".")
    candidate = repo_root.joinpath(*parts).with_suffix(".py")
    if candidate.exists() and not _is_ignored_path(candidate, repo_root, ignored):
        return str(candidate.relative_to(repo_root))

    candidate_init = repo_root.joinpath(*parts, "__init__.py")
    if candidate_init.exists() and not _is_ignored_path(candidate_init, repo_root, ignored):
        return str(candidate_init.relative_to(repo_root))
    return None


def extract_edges(
    file_path: Path,
    repo_root: Path,
    ignored_dirnames: tuple[str, ...] | list[str] | set[str] | None = None,
) -> list[dict]:
    ignored = _excluded_dirnames(ignored_dirnames)
    if _is_ignored_path(file_path, repo_root, ignored):
        return []

    edges: list[dict] = []
    source = str(file_path.relative_to(repo_root))

    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError):
        return edges

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = resolve_import_to_path(alias.name, repo_root, ignored)
                if target and target != source:
                    edges.append({"source": source, "target": target, "relation": "IMPORTS"})

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                if node.level and node.level > 0:
                    pkg_parts = list(file_path.relative_to(repo_root).parent.parts)
                    for _ in range(node.level - 1):
                        if pkg_parts:
                            pkg_parts.pop()
                    module_full = ".".join(pkg_parts + [node.module]) if pkg_parts else node.module
                else:
                    module_full = node.module

                target = resolve_import_to_path(module_full, repo_root, ignored)
                if target and target != source:
                    edges.append({"source": source, "target": target, "relation": "IMPORTS"})

        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_name = None
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr

                if base_name:
                    for edge in edges:
                        if edge["relation"] == "IMPORTS" and edge["source"] == source:
                            edges.append(
                                {
                                    "source": source,
                                    "target": edge["target"],
                                    "relation": "INHERITS",
                                    "meta": f"{node.name} inherits {base_name}",
                                }
                            )
                            break

        elif isinstance(node, ast.Call):
            called_name = None
            if isinstance(node.func, ast.Name):
                called_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called_name = node.func.attr

            if called_name and called_name[0].isupper():
                for edge in edges:
                    if edge["relation"] == "IMPORTS" and edge["source"] == source:
                        edges.append(
                            {
                                "source": source,
                                "target": edge["target"],
                                "relation": "INSTANTIATES",
                                "meta": f"calls {called_name}()",
                            }
                        )
                        break

    seen = set()
    unique_edges = []
    for edge in edges:
        key = (edge["source"], edge["target"], edge["relation"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(edge)
    return unique_edges


def parse_repo(
    repo_root: Path,
    progress_callback: Callable[[int, int], None] | None = None,
    ignored_dirnames: tuple[str, ...] | list[str] | set[str] | None = None,
) -> tuple[list[str], list[dict]]:
    ignored = _excluded_dirnames(ignored_dirnames)
    files = find_python_files(repo_root, ignored)
    rel_files = [str(p.relative_to(repo_root)) for p in files]

    edges: list[dict] = []
    total = len(files)
    for idx, file_path in enumerate(files, start=1):
        edges.extend(extract_edges(file_path, repo_root, ignored))
        if progress_callback:
            progress_callback(idx, total)

    return rel_files, edges
