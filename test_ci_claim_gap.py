"""Tests for ci_claim_gap.

A tool that audits other projects' test coverage has no business shipping
without its own. Every test below pins a wrong answer this tool actually gave
before the fix, so the suite is a record of its mistakes rather than a
restatement of its code.

Run: python -m pytest test_ci_claim_gap.py -q
"""

from __future__ import annotations

import ci_claim_gap as cg


def _zadania(tresc: str, plik: str = "ci.yml"):
    return cg._rozbij_zadania(tresc, plik)


# --- rozpoznawanie krokow testowych -----------------------------------------

def test_rozpoznaje_typowe_uruchomienia_testow():
    for polecenie in (
        "run: pytest tests/",
        "run: bun test",
        "run: npm test",
        "run: cargo test --all",
        "run: go test ./...",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert kroki, f"nie rozpoznano jako testu: {polecenie}"


def test_instalacja_pytesta_nie_jest_uruchomieniem_testow():
    """`pip install pytest` instaluje narzedzie, nie uruchamia testow.

    Odnotowane osobno, bo krok instalacyjny w zadaniu bez testow decyduje
    o tym, czy zglosimy luke platformy.
    """
    kroki, filtry = cg._kroki_testowe_w("run: pip install pytest==9.0.3")
    assert not filtry, "instalacja nie moze byc raportowana jako filtr"


# --- korekta 1: filtr liczy sie tylko PO nazwie narzedzia --------------------

def test_python_m_pytest_nie_jest_filtrem_markerow():
    """`python -m pytest` to sposob uruchomienia, nie filtr `-m`.

    Pierwszy przebieg tego skryptu zgłosil trzy takie linie w repozytorium
    Microsoftu jako `pytest -m`. Wszystkie byly falszywe.
    """
    _, filtry = cg._kroki_testowe_w("run: python -m pytest tests/e2e_python")
    assert not filtry, f"falszywy filtr: {filtry}"


def test_pip_install_z_m_nie_jest_filtrem():
    _, filtry = cg._kroki_testowe_w("run: python3 -m pip install --upgrade pip pytest==9.0.3")
    assert not filtry


def test_prawdziwy_filtr_k_jest_rozpoznany():
    _, filtry = cg._kroki_testowe_w("run: pytest tests/test_lint.py -k lint -v")
    assert [e for e, _ in filtry] == ["pytest -k"]


def test_prawdziwy_filtr_nazwy_jest_rozpoznany():
    _, filtry = cg._kroki_testowe_w(r"run: bun test tests --test-name-pattern '\[windows\]'")
    assert [e for e, _ in filtry] == ["--test-name-pattern"]


# --- korekta 2: delegacja do workflow wielokrotnego uzytku -------------------

WOLAJACY = """
jobs:
  full-tests-matrix:
    strategy:
      matrix:
        os: [Windows, macOS]
        include:
          - os: Windows
            image: windows-2022
    uses: ./.github/workflows/_test.yml
"""

WOLANY = """
jobs:
  test:
    runs-on: ${{ inputs.image }}
    steps:
      - run: pytest tests/
"""


def test_zadanie_delegujace_ma_wykryta_delegacje():
    z = _zadania(WOLAJACY, "full-tests.yml")[0]
    assert z.wywoluje == "_test.yml"
    assert not z.kroki_testowe, "wolajacy nie ma wlasnych krokow - to jest sedno pulapki"


def test_delegacja_nie_jest_raportowana_jako_luka(monkeypatch):
    """NVIDIA NeMo-Guardrails zostala oskarzona o brak testow na Windows i macOS,
    podczas gdy kroki leza w `_test.yml` za wywolaniem `uses:`."""
    tresci = {"full-tests.yml": WOLAJACY, "_test.yml": WOLANY}

    monkeypatch.setattr(cg, "_pobierz", lambda *a, **k: [
        {"name": n, "download_url": n} for n in tresci
    ])
    monkeypatch.setattr(cg, "_tekst", lambda url: tresci[url])

    w = cg.zbadaj("fikcyjny/repo", token=None)
    assert w["luki_platform"] == [], f"delegacja zgloszona jako luka: {w['luki_platform']}"
    assert set(w["systemy_z_testami"]) == {"windows", "macos"}
    assert w["delegujacych"] == 1


# --- korekta 3: luka tylko gdy NIGDZIE nie ma testow na tym systemie ---------

BUDUJE_ALE_NIE_TESTUJE = """
jobs:
  packed-runtime:
    strategy:
      matrix:
        include:
          - os: windows-latest
          - os: macos-latest
    steps:
      - run: bun run build
"""

TESTUJE_NA_WINDOWS = """
jobs:
  test-windows:
    runs-on: windows-latest
    steps:
      - run: bun test tests
"""


def test_build_only_nie_jest_luka_gdy_inny_workflow_testuje(monkeypatch):
    """Zadanie budujace na Windows nie jest oskarzeniem, jesli osobny workflow
    tam testuje. macOS zostaje luka, bo tam nie testuje nikt."""
    tresci = {"ci.yml": BUDUJE_ALE_NIE_TESTUJE, "test-windows.yml": TESTUJE_NA_WINDOWS}

    monkeypatch.setattr(cg, "_pobierz", lambda *a, **k: [
        {"name": n, "download_url": n} for n in tresci
    ])
    monkeypatch.setattr(cg, "_tekst", lambda url: tresci[url])

    w = cg.zbadaj("fikcyjny/repo", token=None)
    luki = [l["system"] for l in w["luki_platform"]]
    assert luki == ["macos"], f"oczekiwano tylko macos, jest {luki}"
    assert "windows" in w["systemy_z_testami"]


def test_luka_wskazuje_gdzie_system_wystepuje(monkeypatch):
    """Raport musi powiedziec, GDZIE system jest wspomniany - inaczej czytelnik
    nie odrozni prawdziwej luki od zadania CodeQL."""
    tresci = {"ci.yml": BUDUJE_ALE_NIE_TESTUJE}
    monkeypatch.setattr(cg, "_pobierz", lambda *a, **k: [
        {"name": n, "download_url": n} for n in tresci
    ])
    monkeypatch.setattr(cg, "_tekst", lambda url: tresci[url])

    w = cg.zbadaj("fikcyjny/repo", token=None)
    for l in w["luki_platform"]:
        assert l["wspomniany_w"], "luka bez wskazania miejsca jest bezuzyteczna"
        assert "ci.yml::packed-runtime" in l["wspomniany_w"]


# --- odpornosc ---------------------------------------------------------------

def test_plik_bez_sekcji_jobs_nie_wywraca_parsera():
    assert _zadania("name: coś\non:\n  push:\n") == []


def test_repozytorium_bez_workflowow_zwraca_blad_nie_wyjatek(monkeypatch):
    monkeypatch.setattr(cg, "_pobierz", lambda *a, **k: {"message": "Not Found"})
    w = cg.zbadaj("fikcyjny/repo", token=None)
    assert "blad" in w


# --- korekta 4: testy uruchamiane przez skrypt lub make ---------------------

def test_skrypt_testowy_liczy_sie_jako_uruchomienie_testow():
    """FastAPI i Typer zostaly zgloszone jako nietestowane na Windows i macOS,
    bo ich krok to `bash scripts/test-cov.sh` w zadaniu nazwanym `test`."""
    for polecenie in (
        "run: uv run --no-sync bash scripts/test-cov.sh $PYTEST_OPTIONS",
        "run: ./scripts/test.sh",
        "run: make test",
        "run: just check",
        "run: python scripts/run_tests.py",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert kroki, f"nie rozpoznano jako testu: {polecenie}"


def test_zwykly_skrypt_budujacy_nie_jest_testem():
    for polecenie in ("run: bash scripts/build.sh", "run: make docs", "run: ./deploy.sh"):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert not kroki, f"blednie uznane za test: {polecenie}"


def test_latest_py_nie_jest_testem():
    """`latest.py` zawiera litery `test`. Wzorzec wymaga nie-litery przed nimi."""
    kroki, _ = cg._kroki_testowe_w("run: python scripts/latest.py")
    assert not kroki


# --- korekta 5: skrypt pakietu jako krok testowy ----------------------------

def test_skrypt_pakietu_liczy_sie_jako_uruchomienie_testow():
    """cc-safety-net dodalo zadania na Windows i macOS wolajace `bun run check:ci`.
    Wzorzec szukajacy `bun test` uznal je za zadania bez testow, wiec narzedzie
    zglosilo luke platformy dokladnie po tym, jak luka zostala zalatana."""
    for polecenie in (
        "run: bun run check:ci",
        "run: npm run test:unit",
        "run: pnpm run check",
        "run: yarn run test",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert kroki, f"nie rozpoznano jako testu: {polecenie}"


def test_skrypt_pakietu_niezwiazany_z_testami_nie_liczy_sie():
    for polecenie in (
        "run: bun run build",
        "run: npm run lint",
        "run: bun run verify:package",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert not kroki, f"blednie uznane za test: {polecenie}"


# --- korekta 6: task runner z nazwanym zadaniem ------------------------------

def test_task_runner_z_nazwanym_zadaniem_liczy_sie():
    """microsoft/autogen ma zadanie `test-autogen-ext-pwsh` na windows-latest,
    ktorego krok to `poe --directory ./packages/autogen-ext test-windows`.
    Zostalo zgloszone jako luka platformy - w zadaniu ze slowem test w nazwie."""
    for polecenie in (
        "run: poe --directory ./packages/autogen-ext test-windows",
        "run: invoke test",
        "run: nox -s tests",
        "run: tox -e check",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert kroki, f"nie rozpoznano jako testu: {polecenie}"


def test_task_runner_bez_zadania_testowego_nie_liczy_sie():
    for polecenie in ("run: poe build", "run: invoke docs", "run: task deploy"):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert not kroki, f"blednie uznane za test: {polecenie}"
