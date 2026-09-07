"""Measure the gap between what a repository's CI claims to cover and what it runs.

Four checks, all learned the hard way while auditing real repositories:

1. PLATFORM GAP - the repository builds or ships on an OS, but no job anywhere
   runs a test suite on that OS.

2. OPT-IN COVERAGE - a test step exists but is filtered by name or marker, so a
   test only covers that lane if somebody remembered to tag it. Nothing fails
   when the tagging step is skipped.

3. GATED TEST STEP - the job's only test step carries an `if:` that can switch
   it off, so the job goes green under a name containing "test" while running
   none. OpenHands/OpenHands#17148 is this shape: `test-and-build (windows)`
   passes having run `npm ci` and `npm run build` and nothing else.

4. SWALLOWED FAILURE - the test step runs but cannot fail the job (`|| true`,
   `continue-on-error: true`), so the green tick carries no information about
   whether the suite passed. withastro/astro runs
   `pnpm test || echo "...not failing CI."`.

All four share one shape: a mechanism that has to be REMEMBERED rather than
enforced. Nothing breaks when someone forgets; the signal quietly stops
meaning anything.

EIGHTEEN corrections are baked in, each from a wrong answer this tool gave
first and each pinned by a test - see README.md for the full table. The three
below are the oldest. Two of the newest came from a single repository: zed was
reported as testing on Linux only, with gaps on Windows and macOS, while
running three full test jobs - because `cargo nextest run` was not recognised
as a test runner, and `namespace-profile-mac-large` was not recognised as a Mac.

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
    # Correction 12: `cargo nextest run`, the default test runner across much
    # of modern Rust. `\btest\b` cannot match inside "nextest", so zed
    # (~60k stars) was reported as testing on Linux only, with platform gaps on
    # Windows and macOS - while running `run_tests_windows`, `run_tests_linux`
    # and `run_tests_mac`, each calling nextest. The one step the pattern did
    # see was `cargo test --workspace --doc` on ubuntu, which is where the
    # false "linux only" came from. `run` is required: `nextest list` only
    # enumerates tests.
    r"|\bcargo[\s-]+nextest\s+run\b"
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

# Correction 13: large projects rarely run on `macos-latest`. zed tests on
# `namespace-profile-mac-large` and `self-32vcpu-windows-2022`, so a pattern
# anchored on the literal `macos` saw `run_tests_mac` as a Linux job and
# reported a macOS platform gap in a repository that runs a full macOS suite.
#
# After `mac`, a separator is REQUIRED. The first version of this used
# `[\w.-]*`, and its own test caught the mistake: that suffix swallows the rest
# of a word, so `machine` and `macro` both registered as macOS. Any workflow
# with a passing mention of "machine" would have looked like a macOS lane.
NIE_LINUX = re.compile(r"\b(windows|macos|mac)(?:[-._][\w.-]*)?\b", re.I)


def _system(s: str) -> str:
    """Normalise a runner label to a platform name. Without this `mac` and
    `macos` become two different platforms, and the gap arithmetic compares
    a set containing one against a set containing the other."""
    s = s.lower()
    return "macos" if s.startswith("mac") else s

# Correction 14: a security scanner is neither a build nor a shipment.
# langfuse was reported as having a macOS platform gap on the strength of macOS
# appearing in `codeql.yml::analyze` alone - CodeQL takes a macOS runner to
# analyse Swift and Objective-C, which says nothing about whether the project
# is released for macOS. The whole check rests on "this repository builds or
# ships on that OS and does not test there", so an OS that only ever appears
# in static analysis cannot support that sentence.
ANALIZA_STATYCZNA = re.compile(r"\b(?:codeql|semgrep|sonar|snyk|trivy|"
                               r"scorecard|dependency-review)\b", re.I)


def _analiza_statyczna(plik: str) -> bool:
    return bool(ANALIZA_STATYCZNA.search(plik))


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
    polyka_blad: bool = False                             # 4. kontrola


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

# Correction 18: a condition on `inputs.` is a PARAMETER, not a hidden switch.
# microsoft/vscode's `pr-darwin-test.yml :: macOS-test` has six test steps, all
# gated on `if: ${{ inputs.unit_tests && ... }}` - which looks like a job that
# can be switched off whole. `pr.yml` calls that workflow repeatedly with
# different subsets:
#
#     job_name: Electron-Unit   electron_tests: true, integration_tests: false
#     job_name: Electron        electron_tests: true, unit_tests: false
#     job_name: Electron-Smoke  ...
#
# Together they cover everything: it is a split into parallel jobs, which is
# good practice rather than a gap. Whether anything is actually disabled
# depends on the CALLERS, and this tool does not read them - so it must not
# accuse. `matrix.` and `needs.` stay reportable: those are internal to the job.
BRAMKA_ZEWNETRZNA = re.compile(r"\binputs\.", re.I)

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
    a leading negation removed, so `!X` and `X` collapse onto each other.

    Correction 15: the negation is not always leading. withastro/astro pairs
    `if: runner.os == 'Linux'` with `if: runner.os != 'Linux'`, which is the
    same complementary shape written with an operator, so `!=` is folded onto
    `==` too. The compared VALUE survives, so `== windows` and `!= macos` stay
    distinct and are still both reported.
    """
    r = w.translate(_ZBEDNE).lower().replace("!=", "==")
    while r.startswith(("!", "not")):
        r = r[3:] if r.startswith("not") else r[1:]
    return r


# A fourth shape, found in withastro/astro on 2026-09-07:
#     run: xvfb-run -a pnpm test || echo "::warning ...not failing CI."
# The suite runs, it may fail, and CI goes green regardless. That is a silent
# failure in its purest form - the green tick carries no information about
# whether the tests passed. `continue-on-error: true` on a test step does
# exactly the same thing in GitHub's own syntax.
#
# Restricted to steps that RUN tests: `|| true` while cleaning up or uploading
# is ordinary practice and says nothing about coverage.
POLYKA_BLAD = re.compile(r"\|\|\s*(?:true|:|echo\b)|^\s*continue-on-error:\s*true\s*$",
                         re.M | re.I)


# Correction 16: some jobs run tests to MEASURE them, not to verify anything.
# dbt-labs/dbt-core has `- name: "Run integration tests and store durations"`
# with `|| true`, inside a workflow called "Update Test Durations"; mlflow does
# the same in `cross-version-tests.yml :: set-matrix`. Swallowing a failure is
# correct there - you want the timing even from a test that failed. Reporting
# it confuses the job's PURPOSE with its content.
ZBIERA_DANE = re.compile(r"\b(?:duration|durations|benchmark|set-matrix|"
                         r"matrix|collect|generate|profile|timing)\b", re.I)


def _zbiera_dane(plik: str, zadanie: str = "") -> bool:
    """True when this workflow or job exists to measure tests, not run them."""
    return bool(ZBIERA_DANE.search(plik) or (zadanie and ZBIERA_DANE.search(zadanie)))


def _polykany_blad(tresc: str) -> bool:
    """True when a step runs tests and cannot fail the job."""
    granice = [m.start() for m in POCZATEK_KROKU.finditer(tresc)]
    if not granice:
        granice = [0]
    for i, poz in enumerate(granice):
        koniec = granice[i + 1] if i + 1 < len(granice) else len(tresc)
        krok = tresc[poz:koniec]
        if not any(_uruchamia_testy(l) for l in krok.splitlines()):
            continue
        if POLYKA_BLAD.search(krok):
            return True
    return False


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
    wolny_test = False                          # test bez zadnego warunku
    for i, poz in enumerate(granice):
        koniec = granice[i + 1] if i + 1 < len(granice) else len(tresc)
        krok = tresc[poz:koniec]
        if not any(_uruchamia_testy(l) for l in krok.splitlines()):
            continue
        m = WARUNEK_KROKU.search(krok)
        if (not m or NIE_BRAMKUJE.match(m.group(1).strip())
                or BRAMKA_ZEWNETRZNA.search(m.group(1))):
            wolny_test = True
            continue
        kandydaci.append((_rdzen_warunku(m.group(1)),
                          " ".join(krok[:m.end() + 60].split())[:160]))

    # Correction 10: a gate sitting next to an UNGATED test step is narrower
    # coverage, not absent coverage. continuedev/continue runs `Run smoke
    # tests` and `Run tests` unconditionally on [ubuntu, windows, macos] and
    # only gates `Run e2e tests` off Windows - a normal and defensible choice,
    # since e2e needs a TTY and is flaky there. OpenHands#17148 is the opposite
    # shape: `- name: Test` is the ONLY test step in the job and it is gated,
    # so switching it off leaves nothing. Conflating the two would make the
    # check say "this platform is untested" about a platform that is tested.
    if wolny_test:
        return []

    # Drop every condition that appears more than once after negation is
    # stripped: that is `X` sitting next to `!X`, which together always run.
    ile = Counter(r for r, _ in kandydaci)
    return [opis for rdzen, opis in kandydaci if ile[rdzen] == 1]


# Correction 11: `cat << 'EOF' > check_discussion.py` CREATES a file; it does
# not run one. continuedev/continue writes exactly that in an issue-triage
# workflow, and the filename pattern claimed it as a test step. The word
# `check` is ambiguous - it means "code quality gate" in `check.sh` and "test
# a condition" in `check_discussion.py` - so the deciding signal has to be
# whether the name is being INVOKED, and a redirect target never is.
PRZEKIEROWANIE = re.compile(r">>?\s*[\w./-]+")


def _uruchamia_testy(linia: str) -> bool:
    """True when the line runs tests, ignoring redirect targets.

    Removing the redirect target keeps `pytest tests/ > out.txt` a test run
    while dropping `cat <<EOF > check_x.py`, because in the first case the
    match is in the command and in the second it is only in the filename.
    """
    return bool(URUCHAMIA_TESTY.search(PRZEKIEROWANIE.sub(" ", linia)))


def _kroki_testowe_w(tresc: str) -> tuple[list[str], list[tuple[str, str]]]:
    kroki, filtry = [], []
    for linia in tresc.splitlines():
        if _uruchamia_testy(linia) or NAZWA_KROKU_TESTOWEGO.search(linia):
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
        z.systemy = sorted({_system(s) for s in NIE_LINUX.findall(blok)})
        z.kroki_testowe, z.filtry = _kroki_testowe_w(blok)
        z.bramkowane = _bramkowane(blok)
        z.polyka_blad = _polykany_blad(blok)
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
                systemy_z_testami.add(_system(s.split("-")[0]))

    # Luka platformy tylko wtedy, gdy NIGDZIE w repo nie ma testow na tym systemie.
    # Correction 14: static-analysis workflows do not count as evidence that
    # the project targets an OS - see `_analiza_statyczna`. They stay in
    # `systemy_z_testami` above, because a scanner running there is still a
    # reason NOT to accuse; they are only barred from raising the accusation.
    systemy_wspomniane: set[str] = set()
    for z in zadania:
        if _analiza_statyczna(z.plik):
            continue
        for s in z.systemy:
            systemy_wspomniane.add(_system(s.split("-")[0]))
    luki = sorted(systemy_wspomniane - systemy_z_testami)

    gdzie_wspomniane = {
        s: sorted({f"{z.plik}::{z.nazwa}" for z in zadania
                   if any(_system(x.split("-")[0]) == s for x in z.systemy)})[:4]
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
        "polykane": [{"plik": z.plik, "zadanie": z.nazwa,
                      "systemy": z.systemy}
                     for z in zadania if z.polyka_blad
                     and not _zbiera_dane(z.plik, z.nazwa)],
    }


# Correction 17: the tool CRASHED MID-REPORT on 2026-09-07, scanning
# microsoft/vscode:
#
#     UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f9ea'
#
# A step there is named "🧪 Run tests", the Windows console encodes in cp1250,
# and `print` took down the whole run partway through a list of repositories.
# Results before vscode were printed; results after it never were. A tool for
# finding silent failures, failing silently in the middle of its own report.
#
# Worse: piped through `tail`, the shell reported exit code 0, so a script
# looping over repositories would have counted that run as a success.
#
# Workflow authors put emoji in step names routinely. The report must survive
# any console encoding, so unrepresentable characters degrade to a marker
# rather than ending the run.
def _bezpiecznie(tekst: str) -> str:
    kod = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        tekst.encode(kod)
        return tekst
    except (UnicodeEncodeError, LookupError):
        return tekst.encode(kod, "replace").decode(kod, "replace")


def _wypisz(w: dict) -> None:
    if "blad" in w:
        print(_bezpiecznie(f"\n{w['repo']}\n  {w['blad']}"))
        return
    print(_bezpiecznie(f"\n{w['repo']}"))
    print(f"  workflows: {w['workflowow']}   jobs: {w['zadan']}"
          f"   delegating: {w['delegujacych']}")
    print(_bezpiecznie(f"  tests run on: {w['systemy_z_testami'] or ['linux only']}"))

    if w["luki_platform"]:
        print(_bezpiecznie("  PLATFORM GAP - the OS appears in CI, but no job anywhere tests on it:"))
        for l in w["luki_platform"]:
            print(_bezpiecznie(f"    {l['system']}  (appears in: {', '.join(l['wspomniany_w'])})"))
    if w["opt_in"]:
        print(_bezpiecznie("  OPT-IN COVERAGE - the test step is filtered, so it covers only tagged tests:"))
        for o in w["opt_in"]:
            gdzie = ", ".join(o["systemy"]) or "linux"
            przez = " (via reusable workflow)" if o["przez_wywolanie"] else ""
            print(_bezpiecznie(f"    {o['plik']} :: {o['zadanie']} ({gdzie}){przez}  [{o['filtr']}]"))
            print(_bezpiecznie(f"      {o['krok']}"))
    if w.get("bramkowane"):
        print(_bezpiecznie("  GATED TEST STEP - the step exists but a condition can switch it off,"))
        print(_bezpiecznie("  so the job can go green under a name containing \"test\":"))
        for b in w["bramkowane"][:8]:
            gdzie = ", ".join(b["systemy"]) or "linux"
            print(_bezpiecznie(f"    {b['plik']} :: {b['zadanie']} ({gdzie})"))
            print(_bezpiecznie(f"      {b['krok']}"))
        if len(w["bramkowane"]) > 8:
            print(_bezpiecznie(f"    ... and {len(w['bramkowane']) - 8} more"))
    if w.get("polykane"):
        print(_bezpiecznie("  SWALLOWED FAILURE - the test step runs but cannot fail the job,"))
        print(_bezpiecznie("  so a green tick says nothing about whether tests passed:"))
        for p in w["polykane"][:6]:
            gdzie = ", ".join(p["systemy"]) or "linux"
            print(_bezpiecznie(f"    {p['plik']} :: {p['zadanie']} ({gdzie})"))
        if len(w["polykane"]) > 6:
            print(_bezpiecznie(f"    ... and {len(w['polykane']) - 6} more"))
    if (not w["luki_platform"] and not w["opt_in"]
            and not w.get("bramkowane") and not w.get("polykane")):
        print(_bezpiecznie("  nothing found"))


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
