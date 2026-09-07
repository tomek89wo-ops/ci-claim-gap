"""Measure the gap between what a repository's CI claims to cover and what it runs.

Two checks, both learned the hard way while auditing real repositories:

1. PLATFORM GAP - the repository builds or ships on an OS, but no job anywhere
   runs a test suite on that OS.

2. OPT-IN COVERAGE - a test step exists but is filtered by name or marker, so a
   test only covers that lane if somebody remembered to tag it. Nothing fails
   when the tagging step is skipped.

Three corrections are baked in, each from a wrong answer this tool gave first:

* Reading one workflow file is not reading CI. An earlier manual audit reported
  a missing Windows job after opening only `ci.yml`; a separate
  `test-windows.yml` existed. Every workflow file is enumerated, never one.

* A filter flag before the tool name is not a filter. `python -m pytest` and
  `pip install -m ...` were both reported as marker filters. Each pattern now
  requires the flag to follow the tool.

* A job that delegates to a reusable workflow still runs tests. NVIDIA's
  NeMo-Guardrails was flagged for a `full-tests-matrix` job covering Windows
  and macOS, whose steps live in `./.github/workflows/_test.yml` behind a
  `uses:` call. Local `uses:` targets are now resolved before judging.

A platform is only reported as a gap when no job in the repository tests on it,
so a build-only job is not an accusation when another workflow covers the same
OS.

Read-only. Uses the public GitHub API. No cloning.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://api.github.com"

# A step that plausibly executes a test suite.
#
# The script and make forms matter as much as the tool names. FastAPI and Typer
# were both reported as untested on Windows and macOS because their test step is
# `bash scripts/test-cov.sh` - a runner this pattern did not recognise, in a job
# named `test`.
URUCHAMIA_TESTY = re.compile(
    r"("
    r"\b(?:pytest|bun\s+test|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+test|"
    r"go\s+test|cargo\s+test|dotnet\s+test|mvn\s+test|gradle\s+test|"
    r"jest|vitest|mocha|tox|nox|unittest|rspec|phpunit|ctest)\b"
    r"|\b(?:make|just|hatch\s+run|rake)\s+(?:test|check)\w*\b"
    # Skrypt pakietu: `bun run check:ci`, `npm run test:unit`. cc-safety-net
    # dodalo zadania na Windows i macOS wolajace `bun run check:ci` i wzorzec
    # szukajacy `bun test` uznal je za zadania bez testow.
    r"|\b(?:bun|npm|yarn|pnpm|deno)\s+run\s+[\w.:-]*(?:test|check)[\w.:-]*"
    # Task runner z nazwanym zadaniem: `poe --directory ./x test-windows`.
    # Microsoft autogen ma zadanie `test-autogen-ext-pwsh` na windows-latest,
    # ktore wolalo `poe ... test-windows` - zgloszone jako luka platformy
    # w zadaniu, ktore ma slowo "test" nawet w nazwie.
    # Correction 9: `\n` must be excluded here. A negated character class
    # matches newlines, so `Invoke-WebRequest ...` on one line and the word
    # `checksum` three lines below joined into a single match - that is how
    # astral-sh/uv's `Install NASM` step, which runs no test at all, was
    # reported as a gated test step.
    r"|\b(?:poe|invoke|inv|task|nox|tox)\b[^|;&\n]*?\s(?:test|check)[\w.:-]*"
    # Nazwa pliku zawierajaca `test`/`check`, ale poprzedzona nie-litera - inaczej
    # `latest.py` liczyloby sie jako testy, a `run_tests.py` by przepadlo.
    r"|[\w./-]*(?:^|[^A-Za-z])(?:test|check)[\w-]*\.(?:sh|bat|ps1|py)\b"
    r")",
    re.I,
)

# A filter that makes coverage opt-in rather than total.
#
# Each pattern requires the filter flag to appear AFTER the tool name. Without
# that anchor, `python -m pytest` and `pip install -m ...` both look like a
# marker filter; both showed up as false positives in this script's first run.
FILTRUJE = [
    (re.compile(r"--test-name-pattern[= ]\s*\S+"), "--test-name-pattern"),
    # Correction 9 applies here too: a negated class matches newlines, so
    # `pytest` in one command and a `-k` belonging to an entirely different
    # command below it would join into one "filtered test run".
    (re.compile(r"\bpytest\b[^|;&\n]*?\s-k\s+\S+"), "pytest -k"),
    (re.compile(r"\bpytest\b[^|;&\n]*?\s-m\s+\S+"), "pytest -m"),
    (re.compile(r"--grep[= ]\s*\S+"), "--grep"),
    (re.compile(r"\bgo\s+test\b[^|;&\n]*?\s-run\s+\S+"), "go test -run"),
    (re.compile(r"--filter[= ]\s*\S+"), "--filter"),
]

NIE_LINUX = re.compile(r"\b(windows|macos)[\w.-]*\b", re.I)

# `uses: ./.github/workflows/foo.yml` - the steps live in another file.
WYWOLANIE_LOKALNE = re.compile(r"^\s*uses:\s*\./\.github/workflows/([\w.-]+\.ya?ml)", re.M)


def _pobierz(url: str, token: str | None) -> object:
    zad = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ci-claim-gap",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(zad, timeout=30) as odp:
        return json.load(odp)


def _tekst(url: str) -> str:
    zad = urllib.request.Request(url, headers={"User-Agent": "ci-claim-gap"})
    with urllib.request.urlopen(zad, timeout=30) as odp:
        return odp.read().decode("utf-8", "replace")


@dataclass
class Zadanie:
    plik: str
    nazwa: str
    systemy: list[str] = field(default_factory=list)
    kroki_testowe: list[str] = field(default_factory=list)
    filtry: list[tuple[str, str]] = field(default_factory=list)
    wywoluje: str | None = None          # delegacja do innego workflow
    testuje_przez_wywolanie: bool = False
    bramkowane: list[str] = field(default_factory=list)   # korekta 8


# Correction 7: the word `test` can live in the step NAME rather than in the
# command. vLLM runs `- name: Smoke test vllm serve` with `run: vllm serve ...`,
# and browser-use has `- name: Set up venv and test for OS/Python versions`;
# both were reported as platform gaps by a pattern that only reads commands.
#
# Only `test` is accepted here, never `check`: `- name: Check out repository`
# opens virtually every workflow in existence, and accepting `check` would make
# the checkout step itself look like a test suite.
NAZWA_KROKU_TESTOWEGO = re.compile(r"^\s*-?\s*name:.*\btest", re.I)

# Correction 8: a test step that exists but is switched off by a condition.
# OpenHands/OpenHands#17148 - job `test-and-build` runs on [ubuntu, windows],
# but `Lint`, `Test` and `Build library` carry `if: matrix.full_checks`, and the
# Windows lane sets `full_checks: false`. The job goes green under a name
# containing the word "test" and runs no test at all.
#
# This check exists because correction 7 would otherwise be a REGRESSION:
# recognising `- name: Test` makes the job look tested, hiding a gap that is
# real. The two corrections only make sense together.
POCZATEK_KROKU = re.compile(r"^\s*-\s+(?:name|uses|run):", re.M)
WARUNEK_KROKU = re.compile(r"^\s*if:\s*(\S.*)$", re.M)

# Correction 8b, measured on this check's own first run against OpenHands: it
# reported ten steps, of which exactly one was real. Two mistakes, both mine.
#
# `always()` is the OPPOSITE of a gate - it means "run even if an earlier step
# failed". Reporting it as a switch-off is precisely backwards. Same for
# `success()` and `!cancelled()`: none of them can turn a step off on one lane
# and leave it on for another, which is the whole shape this check looks for.
NIE_BRAMKUJE = re.compile(r"^\W*(?:always|success|!\s*cancelled)\s*\(\s*\)\W*$")

# Correction 8c, measured across eight repositories on 2026-09-07: the dominant
# false positive is a COMPLEMENTARY PAIR. pydantic and psf/black both run
#
#     - name: Run pytest               if: '!startsWith(matrix.python-version, "pypy")'
#     - name: Run pytest (no coverage)  if: startsWith(matrix.python-version, "pypy")
#
# Between them those cover every case: the suite always runs, just under a
# different command. Calling that "a step that can be switched off" is false.
#
# Detection is by NEGATION, not by count. "There are two of them, so they
# probably complement each other" would hide real gaps in any job that gates
# two different test steps for two different reasons.
_ZBEDNE = str.maketrans("", "", " '\"`${}()")


def _rdzen_warunku(w: str) -> str:
    """Condition stripped to a comparable core: quoting, spacing, `${{ }}` and
    a leading negation removed, so `!X` and `X` collapse onto each other."""
    r = w.translate(_ZBEDNE).lower()
    while r.startswith(("!", "not")):
        r = r[3:] if r.startswith("not") else r[1:]
    return r


def _bramkowane(tresc: str) -> list[str]:
    """Return the test steps that are gated behind an `if:` condition.

    Splits on step boundaries rather than scanning lines, because `if:` belongs
    to whichever step it sits under - a line-by-line scan cannot tell whose
    condition it is.

    A step counts only when its COMMAND runs tests, never when the word "test"
    merely appears in its name: `Upload test artifacts` and `Render test report`
    handle a test's OUTPUT, and a condition on them says nothing about whether
    the suite ran. That distinction is what separates OpenHands#17148 (real)
    from the nine false hits this check produced on its first run.
    """
    granice = [m.start() for m in POCZATEK_KROKU.finditer(tresc)]
    kandydaci: list[tuple[str, str]] = []      # (rdzen warunku, opis kroku)
    for i, poz in enumerate(granice):
        koniec = granice[i + 1] if i + 1 < len(granice) else len(tresc)
        krok = tresc[poz:koniec]
        if not URUCHAMIA_TESTY.search(krok):
            continue
        m = WARUNEK_KROKU.search(krok)
        if not m or NIE_BRAMKUJE.match(m.group(1).strip()):
            continue
        kandydaci.append((_rdzen_warunku(m.group(1)),
                          " ".join(krok[:m.end() + 60].split())[:160]))

    # Drop every condition that appears more than once after negation is
    # stripped: that is `X` sitting next to `!X`, which together always run.
    ile = Counter(r for r, _ in kandydaci)
    return [opis for rdzen, opis in kandydaci if ile[rdzen] == 1]


def _kroki_testowe_w(tresc: str) -> tuple[list[str], list[tuple[str, str]]]:
    kroki, filtry = [], []
    for linia in tresc.splitlines():
        if URUCHAMIA_TESTY.search(linia) or NAZWA_KROKU_TESTOWEGO.search(linia):
            kroki.append(linia.strip()[:160])
            for wzor, etykieta in FILTRUJE:
                if wzor.search(linia):
                    filtry.append((etykieta, linia.strip()[:160]))
    return kroki, filtry


def _rozbij_zadania(tresc: str, plik: str) -> list[Zadanie]:
    """Lekki podzial po zadaniach. Nie parsuje YAML-a - ma byc odporny na
    dowolne formatowanie i nie wymagac zaleznosci."""
    wynik: list[Zadanie] = []
    m_jobs = re.search(r"^jobs:\s*$", tresc, re.M)
    if not m_jobs:
        return wynik
    reszta = tresc[m_jobs.end():]
    granice = [
        (m.start(), m.group(1))
        for m in re.finditer(r"^  ([A-Za-z0-9_-]+):\s*$", reszta, re.M)
    ]
    for i, (poz, nazwa) in enumerate(granice):
        koniec = granice[i + 1][0] if i + 1 < len(granice) else len(reszta)
        blok = reszta[poz:koniec]
        z = Zadanie(plik=plik, nazwa=nazwa)
        z.systemy = sorted({s.lower() for s in NIE_LINUX.findall(blok)})
        z.kroki_testowe, z.filtry = _kroki_testowe_w(blok)
        z.bramkowane = _bramkowane(blok)
        m_uses = WYWOLANIE_LOKALNE.search(blok)
        if m_uses:
            z.wywoluje = m_uses.group(1)
        wynik.append(z)
    return wynik


def zbadaj(repo: str, token: str | None) -> dict:
    try:
        pliki = _pobierz(f"{API}/repos/{repo}/contents/.github/workflows", token)
    except urllib.error.HTTPError as e:
        return {"repo": repo, "blad": f"HTTP {e.code} listing workflows"}
    if not isinstance(pliki, list):
        return {"repo": repo, "blad": "no .github/workflows directory"}

    tresci: dict[str, str] = {}
    for f in pliki:
        if not f["name"].endswith((".yml", ".yaml")):
            continue
        try:
            tresci[f["name"]] = _tekst(f["download_url"])
        except Exception as e:  # noqa: BLE001 - jeden zly plik nie moze przerwac skanu
            print(f"  ! could not read {f['name']}: {type(e).__name__}", file=sys.stderr)

    zadania: list[Zadanie] = []
    for nazwa_pliku, tresc in tresci.items():
        zadania.extend(_rozbij_zadania(tresc, nazwa_pliku))

    # Rozwiaz delegacje: zadanie wolajace inny workflow testuje, jesli TAMTEN testuje.
    for z in zadania:
        if z.wywoluje and z.wywoluje in tresci:
            kroki, filtry = _kroki_testowe_w(tresci[z.wywoluje])
            if kroki:
                z.testuje_przez_wywolanie = True
                z.filtry.extend(filtry)

    systemy_z_testami: set[str] = set()
    for z in zadania:
        if z.kroki_testowe or z.testuje_przez_wywolanie:
            for s in z.systemy:
                systemy_z_testami.add(s.split("-")[0])

    # Luka platformy tylko wtedy, gdy NIGDZIE w repo nie ma testow na tym systemie.
    systemy_wspomniane: set[str] = set()
    for z in zadania:
        for s in z.systemy:
            systemy_wspomniane.add(s.split("-")[0])
    luki = sorted(systemy_wspomniane - systemy_z_testami)

    gdzie_wspomniane = {
        s: sorted({f"{z.plik}::{z.nazwa}" for z in zadania
                   if any(x.split("-")[0] == s for x in z.systemy)})[:4]
        for s in luki
    }

    opt_in = []
    for z in zadania:
        for etykieta, linia in z.filtry:
            opt_in.append({"plik": z.plik, "zadanie": z.nazwa, "systemy": z.systemy,
                           "filtr": etykieta, "krok": linia,
                           "przez_wywolanie": z.testuje_przez_wywolanie})

    bramkowane = [
        {"plik": z.plik, "zadanie": z.nazwa, "systemy": z.systemy, "krok": k}
        for z in zadania for k in z.bramkowane
    ]

    return {
        "repo": repo,
        "workflowow": len(tresci),
        "zadan": len(zadania),
        "delegujacych": sum(1 for z in zadania if z.wywoluje),
        "systemy_z_testami": sorted(systemy_z_testami),
        "luki_platform": [{"system": s, "wspomniany_w": gdzie_wspomniane[s]} for s in luki],
        "opt_in": opt_in,
        "bramkowane": bramkowane,
    }


def _wypisz(w: dict) -> None:
    if "blad" in w:
        print(f"\n{w['repo']}\n  {w['blad']}")
        return
    print(f"\n{w['repo']}")
    print(f"  workflows: {w['workflowow']}   jobs: {w['zadan']}"
          f"   delegating: {w['delegujacych']}")
    print(f"  tests run on: {w['systemy_z_testami'] or ['linux only']}")

    if w["luki_platform"]:
        print("  PLATFORM GAP - the OS appears in CI, but no job anywhere tests on it:")
        for l in w["luki_platform"]:
            print(f"    {l['system']}  (appears in: {', '.join(l['wspomniany_w'])})")
    if w["opt_in"]:
        print("  OPT-IN COVERAGE - the test step is filtered, so it covers only tagged tests:")
        for o in w["opt_in"]:
            gdzie = ", ".join(o["systemy"]) or "linux"
            przez = " (via reusable workflow)" if o["przez_wywolanie"] else ""
            print(f"    {o['plik']} :: {o['zadanie']} ({gdzie}){przez}  [{o['filtr']}]")
            print(f"      {o['krok']}")
    if w.get("bramkowane"):
        print("  GATED TEST STEP - the step exists but a condition can switch it off,")
        print("  so the job can go green under a name containing \"test\":")
        for b in w["bramkowane"][:8]:
            gdzie = ", ".join(b["systemy"]) or "linux"
            print(f"    {b['plik']} :: {b['zadanie']} ({gdzie})")
            print(f"      {b['krok']}")
        if len(w["bramkowane"]) > 8:
            print(f"    ... and {len(w['bramkowane']) - 8} more")
    if not w["luki_platform"] and not w["opt_in"] and not w.get("bramkowane"):
        print("  nothing found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repos", nargs="+", help="owner/name, one or more")
    ap.add_argument("--json", action="store_true", help="raw JSON instead of the report")
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    wyniki = [zbadaj(r, token) for r in a.repos]

    if a.json:
        print(json.dumps(wyniki, indent=2, ensure_ascii=False))
    else:
        for w in wyniki:
            _wypisz(w)
        print("\nThese are LEADS, not verdicts. Read the workflow file and the")
        print("issue tracker before reporting anything. A filter can be deliberate,")
        print("and tests can run somewhere this script cannot see.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
