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
    (re.compile(r"\bpytest\b[^|;&]*?\s-k\s+\S+"), "pytest -k"),
    (re.compile(r"\bpytest\b[^|;&]*?\s-m\s+\S+"), "pytest -m"),
    (re.compile(r"--grep[= ]\s*\S+"), "--grep"),
    (re.compile(r"\bgo\s+test\b[^|;&]*?\s-run\s+\S+"), "go test -run"),
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


def _kroki_testowe_w(tresc: str) -> tuple[list[str], list[tuple[str, str]]]:
    kroki, filtry = [], []
    for linia in tresc.splitlines():
        if URUCHAMIA_TESTY.search(linia):
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

    return {
        "repo": repo,
        "workflowow": len(tresci),
        "zadan": len(zadania),
        "delegujacych": sum(1 for z in zadania if z.wywoluje),
        "systemy_z_testami": sorted(systemy_z_testami),
        "luki_platform": [{"system": s, "wspomniany_w": gdzie_wspomniane[s]} for s in luki],
        "opt_in": opt_in,
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
    if not w["luki_platform"] and not w["opt_in"]:
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
