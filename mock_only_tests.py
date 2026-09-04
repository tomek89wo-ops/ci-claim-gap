"""Find Python tests that assert wiring instead of behaviour.

The shape: a test patches out two or more collaborators, calls the thing under
test, and then asserts only *which* collaborator was constructed or called. It
never asserts what came back. Such a test passes identically whether or not the
two collaborators agree on their output, so a divergence between them is
invisible to it by construction.

This came out of a real report. `guardrails-ai/guardrails` has an open issue
(#1633) where the sync and async validator services return different validated
output and the default path silently discards fixes. The dispatcher tests
covering that code patch both services and assert
`SequentialValidatorService.assert_called_once_with(True)` - so nothing in the
suite could have caught the divergence.

Reports leads, not verdicts. A test that only checks dispatch is a legitimate
unit test when something else checks behaviour. The question this answers is
narrower: does anything in this file look at the value?

    python mock_only_tests.py path/to/tests [more/paths ...]
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

PATCHUJE = ("patch", "patch_object", "setattr")
WYWOLANIE_ASERCJA = (
    "assert_called", "assert_called_once", "assert_called_with",
    "assert_called_once_with", "assert_any_call", "assert_has_calls",
    "assert_not_called", "assert_awaited", "assert_awaited_once",
    "assert_awaited_once_with",
)


@dataclass
class Znalezisko:
    plik: str
    linia: int
    test: str
    ile_patchy: int
    asercje_wywolan: int


def _nazwa_atrybutu(w: ast.AST) -> str:
    """Ostatni czlon w `mocker.patch` albo `mock.patch.object`."""
    if isinstance(w, ast.Attribute):
        return w.attr
    if isinstance(w, ast.Name):
        return w.id
    return ""


class _Skaner(ast.NodeVisitor):
    def __init__(self) -> None:
        self.patche = 0
        self.asercje_wywolan = 0
        self.asercje_wartosci = 0

    def visit_Call(self, node: ast.Call) -> None:
        nazwa = _nazwa_atrybutu(node.func)
        if nazwa in PATCHUJE:
            self.patche += 1
        elif nazwa in WYWOLANIE_ASERCJA:
            self.asercje_wywolan += 1
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        # `assert cos.assert_called()` sie nie zdarza; kazdy inny assert
        # traktujemy jako spojrzenie na wartosc.
        if not (isinstance(node.test, ast.Call)
                and _nazwa_atrybutu(node.test.func) in WYWOLANIE_ASERCJA):
            self.asercje_wartosci += 1
        self.generic_visit(node)


def _testy_w(drzewo: ast.AST):
    for w in ast.walk(drzewo):
        if isinstance(w, (ast.FunctionDef, ast.AsyncFunctionDef)) and w.name.startswith("test"):
            yield w


def zbadaj_plik(sciezka: Path, min_patchy: int = 2) -> list[Znalezisko]:
    try:
        drzewo = ast.parse(sciezka.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []

    wynik: list[Znalezisko] = []
    for fn in _testy_w(drzewo):
        s = _Skaner()
        for w in fn.body:
            s.visit(w)
        if s.patche >= min_patchy and s.asercje_wywolan > 0 and s.asercje_wartosci == 0:
            wynik.append(Znalezisko(
                plik=str(sciezka), linia=fn.lineno, test=fn.name,
                ile_patchy=s.patche, asercje_wywolan=s.asercje_wywolan,
            ))
    return wynik


def zbadaj_sciezke(korzen: Path, min_patchy: int = 2) -> list[Znalezisko]:
    if korzen.is_file():
        return zbadaj_plik(korzen, min_patchy)
    wynik: list[Znalezisko] = []
    for p in sorted(korzen.rglob("*.py")):
        wynik.extend(zbadaj_plik(p, min_patchy))
    return wynik


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("sciezki", nargs="+", help="test files or directories")
    ap.add_argument("--min-patchy", type=int, default=2,
                    help="how many mocks a test needs before it is reported (default 2)")
    a = ap.parse_args()

    wszystkie: list[Znalezisko] = []
    for s in a.sciezki:
        wszystkie.extend(zbadaj_sciezke(Path(s), a.min_patchy))

    if not wszystkie:
        print("nothing found")
        return 0

    print(f"tests asserting wiring only: {len(wszystkie)}\n")
    ostatni = None
    for z in wszystkie:
        if z.plik != ostatni:
            print(z.plik)
            ostatni = z.plik
        print(f"  :{z.linia}  {z.test}"
              f"   (mocks: {z.ile_patchy}, call assertions: {z.asercje_wywolan},"
              f" value assertions: 0)")

    print("\nThese are LEADS. Such a test is fine when something else checks the")
    print("behaviour. The question is whether anything looks at the return value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
