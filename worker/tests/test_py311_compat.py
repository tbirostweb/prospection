"""L'image de production est Python 3.11 ; la machine de développement peut être plus récente (3.12 accepte un antislash ou les mêmes guillemets dans
une expression de f-string, pas 3.11). Un `SyntaxError` à l'import aurait tué tout le pipeline local en production (constaté au build Docker)."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1] / "worker"
FSTRING_EXPR = re.compile(r"""\bf(?P<q>["'])(?:(?!(?P=q)).)*\{[^{}]*\\""")


def test_aucun_antislash_dans_une_expression_de_fstring():
    bad = []
    for f in ROOT.rglob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if FSTRING_EXPR.search(line):
                bad.append(f"{f.relative_to(ROOT.parent)}:{n}")
    assert not bad, f"antislash dans une expression de f-string (SyntaxError en Python 3.11) : {bad}"
