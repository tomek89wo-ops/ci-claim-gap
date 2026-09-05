"""Find environment flags that gate behaviour but no test ever sets.

The shape: a module reads `os.environ.get("SOMETHING_ENABLED")` and branches on
it. The read has tests, the branch it guards has tests — but no test ever sets
the variable, so only one of the two paths is ever exercised. The flag is a
switch nobody has flipped.

This is the generalisation of a failure the author hit in his own system: a
`SENTRY_ENFORCE=1` flag with a working reader, passing tests, and logs reporting
"protection active, 0 violations" every week. Every word of that was true and
meaningless, because no process ever wrote the table the reader read. The flag
configured the last stage of a pipeline that had no first stage.

You cannot detect a missing writer from static text. You can detect the weaker
and still useful thing: a flag whose second state no test has ever entered.

Reports leads, not verdicts. A flag can be deliberately untested — a debug
escape hatch, a vendor switch exercised in staging. The question this answers is
narrower: which switches has nobody flipped in the suite?

    python untested_flags.py path/to/package --tests path/to/tests
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# os.environ.get("X") / os.getenv("X") / os.environ["X"]
CZYTA_SRODOWISKO = ("getenv", "get")

# Zmienne dostarczane przez system operacyjny albo runner CI. Nie sa flagami
# funkcji projektu i nikt ich nie testuje - `APPDATA` byl pierwszym falszywym
# alarmem tego skryptu.
SYSTEMOWE = frozenset({
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMDATA", "SYSTEMROOT",
    "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE", "USERNAME", "COMPUTERNAME",
    "HOME", "PATH", "TEMP", "TMP", "TMPDIR", "SHELL", "USER", "LOGNAME",
    "LANG", "LC_ALL", "PWD", "TERM", "EDITOR", "TZ", "DISPLAY",
    "CI", "GITHUB_ACTIONS", "GITHUB_TOKEN", "GITHUB_REPOSITORY",
    "GITHUB_WORKSPACE", "GITHUB_SHA", "GITHUB_REF", "RUNNER_TEMP", "RUNNER_OS",
    "VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME",
})


@dataclass
class Flaga:
    nazwa: str
    odczyty: list[tuple[str, int]] = field(default_factory=list)
    w_testach: int = 0


def _stala_str(w: ast.AST) -> str | None:
    return w.value if isinstance(w, ast.Constant) and isinstance(w.value, str) else None


class _Zbieracz(ast.NodeVisitor):
    """Zbiera nazwy zmiennych srodowiskowych czytanych w pliku."""

    def __init__(self, plik: str) -> None:
        self.plik = plik
        self.znalezione: list[tuple[str, int]] = []

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        nazwa_f = f.attr if isinstance(f, ast.Attribute) else (
            f.id if isinstance(f, ast.Name) else "")
        if nazwa_f in CZYTA_SRODOWISKO and node.args:
            # os.environ.get(...) albo os.getenv(...) - odrzuc dict.get na czyms innym
            zrodlo = ""
            if isinstance(f, ast.Attribute):
                zrodlo = ast.unparse(f.value) if hasattr(ast, "unparse") else ""
            if nazwa_f == "getenv" or "environ" in zrodlo:
                n = _stala_str(node.args[0])
                if n:
                    self.znalezione.append((n, node.lineno))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        zrodlo = ast.unparse(node.value) if hasattr(ast, "unparse") else ""
        if "environ" in zrodlo:
            n = _stala_str(node.slice)
            if n:
                self.znalezione.append((n, node.lineno))
        self.generic_visit(node)


def _pliki_py(korzen: Path):
    if korzen.is_file():
        yield korzen
        return
    for p in sorted(korzen.rglob("*.py")):
        yield p


def zbadaj(zrodla: Path, testy: Path | None) -> list[Flaga]:
    flagi: dict[str, Flaga] = {}

    for p in _pliki_py(zrodla):
        try:
            drzewo = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        z = _Zbieracz(str(p))
        z.visit(drzewo)
        for nazwa, linia in z.znalezione:
            if nazwa.upper() in SYSTEMOWE:
                continue
            flagi.setdefault(nazwa, Flaga(nazwa)).odczyty.append((str(p), linia))

    if testy is not None and flagi:
        # Zliczamy TEKSTOWO, nie po AST: test moze ustawic flage przez
        # monkeypatch.setenv, fixture, plik .env albo string w parametrize.
        # Wszystkie licza sie jako "ktos jej dotknal".
        for p in _pliki_py(testy):
            try:
                tresc = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for nazwa, f in flagi.items():
                if re.search(r"\b" + re.escape(nazwa) + r"\b", tresc):
                    f.w_testach += 1

    return sorted(flagi.values(), key=lambda f: (-len(f.odczyty), f.nazwa))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("zrodla", help="package or file to scan for environment reads")
    ap.add_argument("--tests", help="test directory; without it, no verdict is given")
    a = ap.parse_args()

    testy = Path(a.tests) if a.tests else None
    if testy is not None and not testy.exists():
        print(f"nie ma katalogu testow: {testy}", file=sys.stderr)
        return 2

    flagi = zbadaj(Path(a.zrodla), testy)
    if not flagi:
        print("no environment reads found")
        return 0

    if testy is None:
        print(f"environment flags read: {len(flagi)}")
        print("(pass --tests to see which of them no test ever touches)\n")
        for f in flagi:
            print(f"  {f.nazwa}   ({len(f.odczyty)} read(s))")
        return 0

    nietkniete = [f for f in flagi if f.w_testach == 0]
    print(f"environment flags read: {len(flagi)}   never mentioned in tests: {len(nietkniete)}\n")
    for f in nietkniete:
        print(f"  {f.nazwa}")
        for plik, linia in f.odczyty[:3]:
            print(f"      {plik}:{linia}")
        if len(f.odczyty) > 3:
            print(f"      ... and {len(f.odczyty) - 3} more")

    if not nietkniete:
        print("  every flag is mentioned somewhere in the tests")

    print("\nThese are LEADS. A flag can be deliberately untested - a debug escape")
    print("hatch, a vendor switch exercised in staging. What this says is only that")
    print("the suite has never entered its second state.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
