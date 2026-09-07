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


# --- korekta 7: test w NAZWIE kroku, nie w komendzie -------------------------
# Zmierzone 2026-09-06 na dwoch repozytoriach naraz: vLLM ma
# `- name: Smoke test vllm serve` z `run: vllm serve ...`, a browser-use
# `- name: Set up venv and test for OS/Python versions`. Oba zostaly zgloszone
# jako luka platformy, bo wzorzec czyta KOMENDE, a slowo "test" bylo w NAZWIE.

def test_nazwa_kroku_z_testem_liczy_sie_jako_test():
    for linia in (
        "      - name: Smoke test vllm serve",
        "      - name: Set up venv and test for OS/Python versions",
        "      - name: Run integration tests",
        "      - name: Test",
    ):
        kroki, _ = cg._kroki_testowe_w(linia)
        assert kroki, f"nie rozpoznano jako testu: {linia}"


def test_checkout_NIE_jest_testem():
    """Najgrozniejszy falszywy pozytyw tej korekty: `- name: Check out
    repository` jest w KAZDYM workflow. Dlatego nazwa kroku uznaje wylacznie
    slowo `test`, nigdy `check`."""
    for linia in (
        "      - name: Check out repository",
        "      - name: Checkout",
        "      - name: Check formatting",
    ):
        kroki, _ = cg._kroki_testowe_w(linia)
        assert not kroki, f"blednie uznane za test: {linia}"


def test_latest_w_nazwie_kroku_nie_jest_testem():
    """`Latest` zawiera litery `test`. Granica slowa musi to odciac —
    ta sama pulapka co `latest.py` w korekcie 3."""
    kroki, _ = cg._kroki_testowe_w("      - name: Download latest release")
    assert not kroki


# --- korekta 8: krok testowy WYLACZONY warunkiem `if:` ----------------------
# OpenHands/OpenHands#17148: zadanie `test-and-build` chodzi na macierzy
# [ubuntu, windows], ale kroki Lint/Test/Build-library maja `if:
# matrix.full_checks`, a windows ma `full_checks: false`. Zadanie swieci na
# zielono pod nazwa "test-and-build" i NIE URUCHAMIA ANI JEDNEGO TESTU.
#
# Bez tej kontroli korekta 7 byłaby REGRESJA: rozpoznanie `- name: Test`
# sprawiloby, ze narzedzie uznaje zadanie za testujace i przestaje zglaszac
# luke, ktora jest PRAWDZIWA.

KROK_ZA_BRAMKA = """
      - name: Test
        if: matrix.full_checks
        run: npm test
"""

KROK_BEZ_BRAMKI = """
      - name: Test
        run: npm test
"""


def test_krok_testowy_za_bramka_if_jest_oznaczony():
    kroki, _ = cg._kroki_testowe_w(KROK_ZA_BRAMKA)
    assert kroki, "krok nadal ma byc widziany jako testowy"
    assert cg._bramkowane(KROK_ZA_BRAMKA), (
        "krok testowy z `if:` musi byc zgloszony — inaczej zadanie o nazwie "
        "'test-and-build' liczy sie jako testujace, choc test jest wylaczony")


def test_krok_testowy_bez_bramki_nie_jest_oznaczony():
    assert not cg._bramkowane(KROK_BEZ_BRAMKI)


def test_bramka_na_kroku_NIETESTOWYM_nie_liczy_sie():
    """`if:` na kroku budujacym jest zwyklym warunkiem, nie ukryciem testu."""
    blok = "      - name: Upload artifact\n        if: always()\n        run: gh release upload x\n"
    assert not cg._bramkowane(blok)


# --- korekta 8b: wlasny falszywy alarm korekty 8 ----------------------------
# Pierwsza wersja `_bramkowane` zglosila w OpenHands dziesiec krokow, z czego
# JEDEN byl prawdziwy. Dwa bledy naraz, oba zmierzone 2026-09-07:
#
#   1. `if: always()` NIE JEST bramka — to jej odwrotnosc. Znaczy "uruchom
#      nawet gdy poprzedni krok padl". Zgloszenie go jako wylacznika jest
#      dokladnie odwrotne do prawdy.
#   2. "test" w nazwie wystarczalo, wiec `- name: Upload test artifacts`
#      i `- name: Render test report` liczyly sie jako kroki testowe. To sa
#      kroki obslugujace WYNIK testu, nie uruchamiajace go.

def test_always_NIE_jest_bramka():
    """`always()` wymusza uruchomienie, wiec nie moze byc powodem alarmu."""
    for warunek in ("if: always()", "if: ${{ always() }}", "if: success()",
                    "if: ${{ !cancelled() }}"):
        blok = f"      - name: Test\n        {warunek}\n        run: npm test\n"
        assert not cg._bramkowane(blok), f"{warunek} nie wylacza kroku"


def test_upload_artefaktow_testowych_nie_jest_krokiem_testowym():
    """`- name: Upload test artifacts` obsluguje WYNIK testu. Bramka na nim
    nie mowi nic o tym, czy testy sie uruchomily."""
    for nazwa in ("Upload test artifacts", "Render test report",
                  "Resolve affected test directories"):
        blok = (f"      - name: {nazwa}\n        if: matrix.full_checks\n"
                f"        uses: actions/upload-artifact@v7\n")
        assert not cg._bramkowane(blok), f"blednie zgloszone: {nazwa}"


def test_prawdziwa_bramka_nadal_lapana():
    """Regresja wprost: OpenHands#17148 musi przezyc obie poprawki."""
    blok = ("      - name: Test\n        if: matrix.full_checks\n"
            "        run: npm test\n")
    assert cg._bramkowane(blok)


# --- korekta 8c: pary komplementarne warunkow -------------------------------
# Zmierzone 2026-09-07 na osmiu repozytoriach: kontrola bramek zglosila
# pydantic, psf/black, fastapi, ruff i uv. Dominujaca klasa falszywek to PARA
# krokow o warunkach bedacych swoimi negacjami:
#
#     - name: Run pytest              if: '!startsWith(matrix.python-version, "pypy")'
#     - name: Run pytest (no coverage) if: startsWith(matrix.python-version, "pypy")
#
# Razem pokrywaja 100% przypadkow — testy uruchamiaja sie ZAWSZE, tylko innym
# poleceniem. Zgloszenie tego jako "krok da sie wylaczyc" jest falszem.

PARA_KOMPLEMENTARNA = """
      - name: Run pytest
        if: '!startsWith(matrix.python-version, ''pypy'')'
        run: make test
      - name: Run pytest (no coverage)
        if: startsWith(matrix.python-version, 'pypy')
        run: uv run pytest
"""

POJEDYNCZA_BRAMKA = """
      - name: Test
        if: matrix.full_checks
        run: npm test
"""


def test_para_komplementarna_NIE_jest_luka():
    """pydantic i psf/black: `if X` obok `if !X` to pelne pokrycie."""
    assert not cg._bramkowane(PARA_KOMPLEMENTARNA), (
        "dwa kroki o przeciwnych warunkach pokrywaja wszystkie przypadki")


def test_pojedyncza_bramka_nadal_jest_luka():
    """OpenHands#17148 nie ma pary — `if: matrix.full_checks` i nic wiecej."""
    assert cg._bramkowane(POJEDYNCZA_BRAMKA)


def test_para_o_ROZNYCH_warunkach_nadal_zglaszana():
    """Dwa bramkowane kroki to jeszcze nie para. Musza byc swoimi negacjami —
    inaczej heurystyka 'jest ich dwa, wiec pewnie sie uzupelniaja' ukrywalaby
    prawdziwe luki."""
    blok = ("      - name: Test unit\n        if: matrix.full_checks\n        run: npm test\n"
            "      - name: Test e2e\n        if: matrix.slow\n        run: npm test:e2e\n")
    assert len(cg._bramkowane(blok)) == 2


# --- korekta 9: wzorzec przeskakiwal znak nowej linii ------------------------
# astral-sh/uv, krok `- name: "Install NASM"` w build-release-binaries.yml —
# nie ma w nim zadnego testu. Zostal zlapany, bo wzorzec task-runnera
#     \b(?:poe|invoke|inv|task|nox|tox)\b[^|;&]*?\s(?:test|check)
# uzywa NEGOWANEJ klasy znakow, a ta obejmuje `\n`. Skrypt PowerShella zawiera
# `Invoke-WebRequest ...` w jednej linii i `throw 'NASM installer checksum
# mismatch'` trzy linie nizej — dopasowanie przeskoczylo przez caly blok.
#
# Ta sama wada byla w KAZDYM wzorcu filtra (`pytest -k`, `pytest -m`,
# `go test -run`), wiec `pytest` w jednej linii i `-k` w zupelnie innej
# komendzie liczylyby sie jako filtr.

NASM = """      - name: "Install NASM"
        if: contains(matrix.platform.target, 'x86')
        run: |
          $installer = Join-Path $env:RUNNER_TEMP "nasm-installer-x64.exe"
          Invoke-WebRequest "https://www.nasm.us/pub/nasm/x.exe" -OutFile $installer
          if ((Get-FileHash $installer -Algorithm SHA256).Hash -ne $sha256) {
            throw 'NASM installer checksum mismatch'
          }
"""


def test_invoke_w_jednej_linii_i_check_w_innej_to_NIE_test():
    kroki, _ = cg._kroki_testowe_w(NASM)
    assert not kroki, f"blednie uznane za krok testowy: {kroki}"
    assert not cg._bramkowane(NASM)


def test_task_runner_w_JEDNEJ_linii_nadal_dziala():
    """Regresja korekty 6: `poe ... test-windows` musi przezyc zawezenie."""
    kroki, _ = cg._kroki_testowe_w("run: poe --directory ./python/packages test-windows")
    assert kroki


def test_filtr_nie_sklada_sie_z_dwoch_roznych_linii():
    """`pytest` w jednej komendzie i `-k` w innej to nie jest filtr."""
    blok = "        run: |\n          pytest tests/\n          grep -k foo bar\n"
    _, filtry = cg._kroki_testowe_w(blok)
    assert not any(e == "pytest -k" for e, _ in filtry), filtry


# --- korekta 10: bramka obok NIEBRAMKOWANEGO testu to zawezenie, nie luka ---
# continuedev/continue, zadanie `test` na macierzy [ubuntu, windows, macos]:
#     - name: Run smoke tests        (bez warunku)
#     - name: Run tests              (bez warunku)
#     - name: Run e2e tests          if: matrix.os != 'windows-latest'
# Windows JEST testowany — wylaczone sa wylacznie testy e2e, co jest zwykla
# i uzasadniona praktyka (e2e potrzebuja TTY i sa chwiejne na Windows).
#
# OpenHands#17148 wyglada inaczej i o to chodzi: tam `- name: Test` jest
# JEDYNYM krokiem testowym w zadaniu i jest bramkowany, wiec po jego wylaczeniu
# nie zostaje nic. Bez tego rozroznienia kontrola myli "wezsze pokrycie"
# z "zerowym pokryciem" — a to sa dwa rozne zdania o swiecie.

ZAWEZENIE = """
      - name: Run tests
        run: npm test
      - name: Run e2e tests
        if: matrix.os != 'windows-latest'
        run: npm run test:e2e
"""

LUKA = """
      - name: Build app
        run: npm run build
      - name: Test
        if: matrix.full_checks
        run: npm test
"""


def test_bramka_obok_niebramkowanego_testu_to_NIE_luka():
    assert not cg._bramkowane(ZAWEZENIE), (
        "zadanie ma niebramkowany `npm test`, wiec platforma JEST testowana")


def test_bramka_na_JEDYNYM_kroku_testowym_to_luka():
    """OpenHands#17148 — po wylaczeniu nie zostaje zaden test."""
    assert cg._bramkowane(LUKA)


# --- korekta 11: przekierowanie tworzy plik, nie uruchamia go ---------------
# continuedev/continue, workflow similar-issues.yml:
#     cat << 'PYTHON_SCRIPT' > check_discussion.py
# Wzorzec nazwy pliku zlapal `check_discussion.py`. To skrypt bota do issue
# ("sprawdz, czy zgloszenie powinno byc dyskusja"), a nie test — i nie jest
# w tej linii URUCHAMIANY, tylko TWORZONY. Slowo `check` jest dwuznaczne:
# znaczy raz "kontrola jakosci kodu", raz "sprawdz warunek".

def test_przekierowanie_do_pliku_nie_jest_uruchomieniem():
    for linia in (
        "          cat << 'PYTHON_SCRIPT' > check_discussion.py",
        "        run: echo x >> test_helper.py",
    ):
        kroki, _ = cg._kroki_testowe_w(linia)
        assert not kroki, f"tworzenie pliku uznane za uruchomienie: {linia}"


def test_URUCHOMIENIE_skryptu_testowego_nadal_dziala():
    """Regresja korekty 4: FastAPI wola `bash scripts/test-cov.sh`."""
    for linia in ("run: bash scripts/test-cov.sh",
                  "run: python scripts/run_tests.py",
                  "run: ./check.sh"):
        kroki, _ = cg._kroki_testowe_w(linia)
        assert kroki, f"przestalo byc rozpoznawane: {linia}"


def test_uruchomienie_obok_przekierowania_liczy_sie():
    """`pytest > wynik.txt` to nadal uruchomienie testow."""
    kroki, _ = cg._kroki_testowe_w("run: pytest tests/ > wyniki.txt")
    assert kroki


# --- korekta 12: cargo nextest, standardowy runner Rusta --------------------
# zed-industries/zed (~60k gwiazdek) zostal zgloszony jako testowany WYLACZNIE
# na Linuksie, z luka platformy na Windows i macOS. Nieprawda: repo ma
# `run_tests_windows`, `run_tests_linux` i `run_tests_mac`, kazdy wolajacy
#     cargo nextest run --workspace --no-fail-fast
# Wzorzec mial `cargo\s+test`, a to jest `cargo nextest` — i `\btest\b` nie
# dopasuje sie w srodku slowa "nextest". Jedynym rozpoznanym krokiem byl
# `cargo test --workspace --doc` w zadaniu doctests na ubuntu, stad falszywe
# "linux only" przy trzech pelnych zadaniach testowych.
#
# To jest najszersza z dotychczasowych fałszywek: nextest jest dzis domyslnym
# runnerem w duzej czesci ekosystemu Rusta, wiec ta luka dotyczyla KAZDEGO
# nowoczesnego repo w tym jezyku.

def test_cargo_nextest_liczy_sie_jako_uruchomienie_testow():
    for polecenie in (
        "run: cargo nextest run --workspace --no-fail-fast --no-tests=warn",
        "run: cargo nextest run -E 'package(foo)'",
        "run: cargo-nextest run --workspace",
    ):
        kroki, _ = cg._kroki_testowe_w(polecenie)
        assert kroki, f"nie rozpoznano jako testu: {polecenie}"


def test_cargo_test_nadal_dziala():
    """Regresja: `cargo test --workspace --doc` musi zostac rozpoznane."""
    kroki, _ = cg._kroki_testowe_w("run: cargo test --workspace --doc --no-fail-fast")
    assert kroki


def test_nextest_bez_run_to_nie_uruchomienie():
    """`cargo nextest list` wypisuje testy, nie uruchamia ich."""
    kroki, _ = cg._kroki_testowe_w("run: cargo nextest list")
    assert not kroki


# --- korekta 13: niestandardowe nazwy runnerow ------------------------------
# Po korekcie 12 zed nadal mial zgloszona luke na macOS, bo jego runner nazywa
# sie `namespace-profile-mac-large` — jest tam `mac`, nie ma `macos`. Zadanie
# `run_tests_mac` wola nextest na tej maszynie, wiec macOS JEST testowany.
# Duze projekty rzadko uzywaja `macos-latest`; jada na wlasnych albo
# wynajetych runnerach o dowolnych nazwach.

def test_runner_z_samym_mac_liczy_sie_jako_macos():
    zadania = cg._rozbij_zadania(
        "jobs:\n"
        "  run_tests_mac:\n"
        "    runs-on: namespace-profile-mac-large\n"
        "    steps:\n"
        "      - run: cargo nextest run --workspace\n",
        "run_tests.yml")
    assert zadania, "zadanie nie zostalo rozpoznane"
    assert "macos" in zadania[0].systemy, zadania[0].systemy


def test_self_hosted_windows_tez_liczy_sie():
    zadania = cg._rozbij_zadania(
        "jobs:\n"
        "  run_tests_windows:\n"
        "    runs-on: self-32vcpu-windows-2022\n"
        "    steps:\n"
        "      - run: cargo nextest run --workspace\n",
        "run_tests.yml")
    assert any(s.startswith("windows") for s in zadania[0].systemy), zadania[0].systemy


def test_machine_i_macro_to_NIE_macos():
    """Granica slowa musi odciac `machine`, `macro`, `mach`. Bez tego kazdy
    `runs-on: ubuntu-latest` z komentarzem o 'machine' udawalby macOS."""
    zadania = cg._rozbij_zadania(
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest   # big machine, macro expansion\n"
        "    steps:\n"
        "      - run: pytest\n",
        "ci.yml")
    assert not zadania[0].systemy, zadania[0].systemy


# --- korekta 14: analiza statyczna to nie dystrybucja ------------------------
# langfuse dostal zgloszona luke platformy na macOS, bo macOS pojawia sie tam
# WYLACZNIE w `codeql.yml::analyze`. CodeQL bierze macOS-owy runner, zeby
# analizowac Swift/ObjC — to nie znaczy, ze projekt jest na macOS wydawany.
#
# Cala kontrola opiera sie na zdaniu "repo BUDUJE albo WYSYLA na ten system,
# a tam nie testuje". Skaner bezpieczenstwa nie jest ani buildem, ani wysylka,
# wiec nie moze byc przeslanka tego zdania.

def test_macos_tylko_w_codeql_to_NIE_luka():
    assert cg._analiza_statyczna("codeql.yml")
    assert cg._analiza_statyczna("codeql-analysis.yml")
    assert cg._analiza_statyczna("semgrep.yml")


def test_zwykle_workflow_nie_sa_analiza_statyczna():
    for f in ("ci.yml", "release.yml", "desktop-macos.yml", "build.yaml"):
        assert not cg._analiza_statyczna(f), f


# --- korekta 15: para komplementarna z == / != ------------------------------
# withastro/astro ma w jednym zadaniu:
#     - name: Test (Linux)  if: runner.os == 'Linux'
#     - name: Test          if: runner.os != 'Linux'
# Razem pokrywaja wszystko. Korekta 8c tego nie zlapala, bo usuwa tylko
# WIODACA negacje (`!X` vs `X`), a tu negacja siedzi w operatorze.

def test_para_z_operatorem_rowna_sie_i_rozne_od():
    blok = ("      - name: Test (Linux)\n        if: runner.os == 'Linux'\n"
            "        run: pnpm test\n"
            "      - name: Test\n        if: runner.os != 'Linux'\n"
            "        run: pnpm test\n")
    assert not cg._bramkowane(blok), "to jest pelne pokrycie, nie luka"


def test_rozne_wartosci_to_NIE_para():
    """`== windows` i `!= macos` nie pokrywaja sie nawzajem."""
    blok = ("      - name: Test A\n        if: matrix.os == 'windows'\n"
            "        run: pnpm test\n"
            "      - name: Test B\n        if: matrix.os != 'macos'\n"
            "        run: pnpm test\n")
    assert len(cg._bramkowane(blok)) == 2


# --- NOWA KONTROLA: test, ktorego niepowodzenie jest POLYKANE ---------------
# withastro/astro:
#     run: xvfb-run -a pnpm test || echo "::warning ...Known flaky; not failing CI."
# Test sie uruchamia, moze paść, i CI przechodzi. To jest cicha awaria
# w najczystszej postaci: zielony znaczek nie niesie zadnej informacji
# o tym, czy testy przeszly.
#
# `continue-on-error: true` na kroku testowym robi doklanie to samo, tylko
# skladnia GitHuba zamiast powloki.

def test_polkniety_blad_przez_or_echo():
    blok = "      - name: Test\n        run: pnpm test || echo 'flaky, ignoring'\n"
    assert cg._polykany_blad(blok)


def test_polkniety_blad_przez_or_true():
    assert cg._polykany_blad("      - name: Test\n        run: pytest || true\n")


def test_polkniety_blad_przez_continue_on_error():
    blok = ("      - name: Test\n        continue-on-error: true\n"
            "        run: pytest\n")
    assert cg._polykany_blad(blok)


def test_zwykly_krok_testowy_nie_polyka():
    assert not cg._polykany_blad("      - name: Test\n        run: pytest -v\n")


def test_polykanie_na_kroku_NIETESTOWYM_nie_liczy_sie():
    """`|| true` przy sprzataniu albo uploadzie to normalna praktyka —
    kontrola dotyczy wylacznie krokow, ktore URUCHAMIAJA testy."""
    blok = ("      - name: Cleanup\n        run: rm -rf tmp || true\n")
    assert not cg._polykany_blad(blok)


def test_continue_on_error_false_nie_polyka():
    blok = ("      - name: Test\n        continue-on-error: false\n"
            "        run: pytest\n")
    assert not cg._polykany_blad(blok)


# --- korekta 16: zadanie ZBIERA DANE o testach, nie weryfikuje --------------
# dbt-labs/dbt-core, workflow "Update Test Durations":
#     - name: "Run integration tests and store durations"
#       run: ... || true
# mlflow ma to samo w `cross-version-tests.yml :: set-matrix`.
#
# Oba URUCHAMIAJA testy, ale po to, zeby zmierzyc czasy albo zbudowac macierz —
# nie po to, zeby cokolwiek zweryfikowac. `|| true` jest tam SLUSZNE: czasy
# chcemy takze z testu, ktory padl. Zgloszenie tego jako "polkniety blad"
# myli cel zadania z jego trescia.

def test_zadanie_mierzace_czasy_nie_jest_polknietym_bledem():
    """Pierwsza wersja tego testu wymieniala tu `cross-version-tests.yml`
    i padla slusznie: ta nazwa pliku nie niesie zadnego sygnalu o zbieraniu
    danych — niesie go dopiero nazwa ZADANIA (`set-matrix`), sprawdzana
    w tescie nizej. Poprawiony zostal test, nie kod."""
    for plik in ("update-test-durations.yml", "benchmark.yml",
                 "collect-durations.yaml", "generate-timings.yml"):
        assert cg._zbiera_dane(plik), plik


def test_zwykle_workflow_testowe_nie_sa_zbieraniem_danych():
    for plik in ("ci.yml", "test.yml", "tests.yaml", "test-windows.yml"):
        assert not cg._zbiera_dane(plik), plik


def test_nazwa_zadania_tez_sie_liczy():
    """mlflow trzyma to w zadaniu `set-matrix`, nie w nazwie pliku."""
    assert cg._zbiera_dane("ci.yml", "set-matrix")
    assert cg._zbiera_dane("ci.yml", "generate-durations")
    assert not cg._zbiera_dane("ci.yml", "test-and-build")


# --- korekta 17: narzedzie WYWRACALO SIE na emoji w kroku -------------------
# Zmierzone 2026-09-07 na microsoft/vscode:
#
#     UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f9ea'
#
# Krok nazywa sie "🧪 Run tests", konsola Windows uzywa cp1250, a `print`
# wywrocil caly skan W POLOWIE listy repozytoriow. Wyniki repo przed vscode
# zdazyly sie wypisac, wyniki po nim — nigdy. Narzedzie do wykrywania cichych
# awarii padajace po cichu w srodku raportu to najgorszy mozliwy wariant.
#
# Gorzej: przy `| tail` powloka zwrocila kod 0, wiec skrypt wolajacy to
# w petli uznalby przebieg za udany.

import io
import sys


def _konsola_cp1250():
    """Strumien zachowujacy sie jak konsola Windows w cp1250.

    `capsys` nie odtworzy tej awarii — pytest przechwytuje stdout jako UTF-8,
    wiec test na nim przechodzi na kodzie, ktory w prawdziwej konsoli pada.
    Pierwsza wersja tych dwoch testow byla wlasnie taka i przeszla na
    niezmienionym, wadliwym kodzie.
    """
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1250", errors="strict")


def test_emoji_w_kroku_nie_wywraca_wypisywania(monkeypatch):
    w = {"repo": "x/y", "workflowow": 1, "zadan": 1, "delegujacych": 0,
         "systemy_z_testami": [], "luki_platform": [], "opt_in": [],
         "bramkowane": [{"plik": "ci.yml", "zadanie": "test",
                         "systemy": ["windows"],
                         "krok": "- name: 🧪 Run tests if: matrix.slow"}],
         "polykane": []}
    monkeypatch.setattr(sys, "stdout", _konsola_cp1250())
    cg._wypisz(w)                      # nie moze rzucic UnicodeEncodeError


def test_emoji_w_nazwie_repo_tez_przechodzi(monkeypatch):
    monkeypatch.setattr(sys, "stdout", _konsola_cp1250())
    cg._wypisz({"repo": "x/✨y", "blad": "HTTP 404"})


# --- korekta 18: bramka na `inputs.` w reusable workflow ---------------------
# microsoft/vscode, `pr-darwin-test.yml :: macOS-test`: SZESC krokow testowych,
# kazdy bramkowany przez `if: ${{ inputs.unit_tests && ... }}`. Wyglada jak
# zadanie, ktore da sie wylaczyc w calosci.
#
# `pr.yml` wywoluje ten workflow WIELOKROTNIE, za kazdym razem z innym
# podzbiorem:
#     job_name: Electron-Unit   electron_tests: true, integration: false
#     job_name: Electron        electron_tests: true, unit_tests: false
#     job_name: Electron-Smoke  ...
# Razem pokrywaja calosc — to jest rozbicie na rownolegle zadania, czyli DOBRA
# praktyka, a nie ukryty wylacznik.
#
# Reguła: warunek odwolujacy sie do `inputs.` jest PARAMETREM sterowanym przez
# wywolujacego, nie bramka wewnetrzna. Bez sprawdzenia wywolujacych nie da sie
# orzec, czy cokolwiek jest wylaczone — a narzedzie ich nie czyta.

def test_bramka_na_inputs_nie_jest_zgłaszana():
    blok = ("      - name: Run unit tests\n"
            "        if: ${{ inputs.unit_tests && inputs.electron_tests }}\n"
            "        run: npm run test-node\n")
    assert not cg._bramkowane(blok), (
        "`inputs.` steruje wywolujacy — to parametr, nie ukryta bramka")


def test_bramka_na_matrix_NADAL_jest_zgłaszana():
    """Regresja OpenHands#17148: `matrix.` jest wewnetrzne dla zadania,
    wiec nadal liczy sie jako bramka."""
    blok = ("      - name: Test\n        if: matrix.full_checks\n"
            "        run: npm test\n")
    assert cg._bramkowane(blok)


def test_bramka_na_needs_nadal_jest_zgłaszana():
    blok = ("      - name: Test\n"
            "        if: needs.changes.outputs.src == 'true'\n"
            "        run: pytest\n")
    assert cg._bramkowane(blok)
