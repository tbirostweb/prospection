#!/usr/bin/env python3
"""Garde-fou sur les migrations SQL (db/updates/*.sql).

Attrape, SANS serveur MySQL, les pièges qui ont déjà cassé un déploiement :

1. `SELECT <constantes> ... WHERE` sans `FROM` -> erreur de syntaxe MySQL
   (valide en PostgreSQL, d'où la confusion). Il faut `FROM DUAL`.
2. Un `;` à l'intérieur d'une chaîne -> casserait le découpage en instructions
   du runner (worker/migrate.py découpe naïvement sur `;`).
3. Un antislash dans une chaîne SQL -> MySQL l'interprète comme un échappement :
   `'{"q": "\\"texte\\""}'` perd ses `\\` et devient du JSON invalide.
4. Fichier vide / non numéroté.

Vérifie db/updates/ (migrations) ET db/migrations/ (schéma initial + seed, joués
sur toute installation neuve : une erreur y empêche MySQL de démarrer).

Usage :  python3 scripts/check_migrations.py
Sortie non nulle si un problème est détecté.
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
UPDATES = ROOT / "db" / "updates"
INIT = ROOT / "db" / "migrations"


def split_statements(sql: str) -> list[str]:
    """Même découpage que worker/migrate.py (volontairement identique)."""
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def strip_strings(stmt: str) -> str:
    """Remplace le contenu des chaînes par des blancs (analyse hors littéraux)."""
    return re.sub(r"'(?:[^']|'')*'", "''", stmt)


def check_select_without_from(stmt: str) -> str | None:
    """Un SELECT porteur d'un WHERE doit avoir un FROM (MySQL)."""
    code = strip_strings(stmt)
    if not re.search(r"\bSELECT\b", code, re.I):
        return None
    # On ne regarde que le SELECT de premier niveau : on retire les sous-requêtes.
    top = re.sub(r"\([^()]*\)", " ", code)
    while re.search(r"\([^()]*\)", top):
        top = re.sub(r"\([^()]*\)", " ", top)
    if not re.search(r"\bSELECT\b", top, re.I):
        return None
    if re.search(r"\bWHERE\b", top, re.I) and not re.search(r"\bFROM\b", top, re.I):
        return "SELECT avec WHERE mais sans FROM -> ajouter `FROM DUAL` (MySQL)"
    return None


def check_semicolon_in_string(stmt: str) -> str | None:
    for literal in re.findall(r"'(?:[^']|'')*'", stmt):
        if ";" in literal:
            return "`;` dans une chaîne -> casserait le découpage des instructions"
    return None


def check_backslash_in_string(stmt: str) -> str | None:
    for literal in re.findall(r"'(?:[^'\\]|''|\\.)*'", stmt):
        if "\\" in literal:
            return ("antislash dans une chaîne SQL : MySQL l'interprète comme un échappement "
                    "(JSON invalide) -> retirer les \\\" ou utiliser JSON_OBJECT/JSON_ARRAY")
    return None


def main() -> int:
    if not UPDATES.is_dir():
        print(f"✗ dossier introuvable : {UPDATES}")
        return 1

    files = sorted(UPDATES.glob("*.sql"))
    init_files = sorted(INIT.glob("*.sql")) if INIT.is_dir() else []
    if not files:
        print("✗ aucune migration trouvée")
        return 1

    problems = 0
    for f in init_files + files:
        sql = f.read_text(encoding="utf-8")
        statements = split_statements(sql)
        issues: list[str] = []

        if not statements:
            issues.append("fichier sans instruction exploitable")
        if f.parent == UPDATES and not re.match(r"^\d{3}_", f.name):
            issues.append("nom non numéroté (attendu : 003_xxx.sql)")

        for i, stmt in enumerate(statements, 1):
            for check in (check_select_without_from, check_semicolon_in_string,
                          check_backslash_in_string):
                msg = check(stmt)
                if msg:
                    issues.append(f"instruction #{i} : {msg}\n      {stmt[:90]}…")

        if issues:
            problems += len(issues)
            print(f"✗ {f.parent.name}/{f.name}")
            for it in issues:
                print(f"    - {it}")
        else:
            print(f"✓ {f.parent.name}/{f.name} ({len(statements)} instructions)")

    if problems:
        print(f"\n{problems} problème(s) détecté(s).")
        return 1
    print(f"\nOK — {len(init_files) + len(files)} fichiers SQL valides.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
