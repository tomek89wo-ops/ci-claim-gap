"""Tests for untested_flags.

Run: python -m pytest test_untested_flags.py -q
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import untested_flags as uf


def _drzewo(tmp_path: Path, zrodlo: str, test: str | None = None):
    src = tmp_path / "src"
    src.mkdir()
    (src / "modul.py").write_text(textwrap.dedent(zrodlo), encoding="utf-8")
    if test is None:
        return src, None
    tst = tmp_path / "tests"
    tst.mkdir()
    (tst / "test_modul.py").write_text(textwrap.dedent(test), encoding="utf-8")
    return src, tst


# --- rozpoznawanie odczytow --------------------------------------------------

def test_rozpoznaje_trzy_formy_odczytu(tmp_path):
    src, tst = _drzewo(tmp_path, """
        import os
        a = os.environ.get("FLAGA_A")
        b = os.getenv("FLAGA_B")
        c = os.environ["FLAGA_C"]
    """, "pass\n")
    nazwy = {f.nazwa for f in uf.zbadaj(src, tst)}
    assert nazwy == {"FLAGA_A", "FLAGA_B", "FLAGA_C"}


def test_zwykly_slownik_get_nie_jest_odczytem_srodowiska(tmp_path):
    """`d.get("klucz")` na zwyklym slowniku nie jest flaga."""
    src, tst = _drzewo(tmp_path, """
        d = {"klucz": 1}
        x = d.get("NIE_FLAGA")
    """, "pass\n")
    assert uf.zbadaj(src, tst) == []


def test_zmienne_systemowe_sa_pomijane(tmp_path):
    """`APPDATA` byl pierwszym falszywym alarmem tego skryptu - zmienna dana
    przez system, nie przelacznik projektu."""
    src, tst = _drzewo(tmp_path, """
        import os
        a = os.environ.get("APPDATA")
        b = os.getenv("HOME")
        c = os.environ.get("GITHUB_TOKEN")
        d = os.environ.get("MOJA_FLAGA")
    """, "pass\n")
    nazwy = {f.nazwa for f in uf.zbadaj(src, tst)}
    assert nazwy == {"MOJA_FLAGA"}


# --- werdykt o pokryciu ------------------------------------------------------

def test_flaga_wspomniana_w_testach_nie_jest_zglaszana(tmp_path):
    src, tst = _drzewo(tmp_path, """
        import os
        if os.environ.get("TRYB_SCISLY") == "1":
            pass
    """, """
        def test_tryb(monkeypatch):
            monkeypatch.setenv("TRYB_SCISLY", "1")
    """)
    f = uf.zbadaj(src, tst)[0]
    assert f.w_testach == 1


def test_flaga_nietknieta_przez_testy_ma_zero(tmp_path):
    src, tst = _drzewo(tmp_path, """
        import os
        if os.environ.get("NIKT_MNIE_NIE_TESTUJE") == "1":
            pass
    """, """
        def test_cos_innego():
            assert True
    """)
    f = uf.zbadaj(src, tst)[0]
    assert f.w_testach == 0


def test_liczy_wzmianke_w_dowolnej_formie(tmp_path):
    """Test moze ustawic flage przez setenv, fixture, parametrize albo goly
    string. Kazda z tych form znaczy 'ktos jej dotknal'."""
    src, tst = _drzewo(tmp_path, """
        import os
        x = os.environ.get("FLAGA_X")
    """, """
        import pytest
        @pytest.mark.parametrize("nazwa", ["FLAGA_X"])
        def test_param(nazwa):
            assert nazwa
    """)
    assert uf.zbadaj(src, tst)[0].w_testach == 1


def test_nazwa_bedaca_fragmentem_innej_nie_liczy_sie(tmp_path):
    """`FLAGA` nie moze byc zaliczona przez wzmianke o `FLAGA_ROZSZERZONA`."""
    src, tst = _drzewo(tmp_path, """
        import os
        x = os.environ.get("FLAGA")
    """, """
        def test_inna():
            assert "FLAGA_ROZSZERZONA"
    """)
    assert uf.zbadaj(src, tst)[0].w_testach == 0


# --- odpornosc ---------------------------------------------------------------

def test_bez_katalogu_testow_nie_ma_werdyktu(tmp_path):
    src, _ = _drzewo(tmp_path, """
        import os
        x = os.environ.get("FLAGA")
    """)
    f = uf.zbadaj(src, None)[0]
    assert f.w_testach == 0 and f.odczyty, "bez testow zbieramy odczyty, nie sadzimy"


def test_plik_z_bledem_skladni_nie_wywraca_skanera(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "zly.py").write_text("def f(:\n", encoding="utf-8")
    assert uf.zbadaj(src, None) == []


# --- korekta 20: klucz API to nie jest flaga bramkujaca zachowanie ----------
# Zmierzone 2026-09-07 na guardrails i mem0. WSZYSTKIE trafienia byly tej
# samej postaci:
#     SARVAM_API_KEY, XAI_API_KEY, VLLM_API_KEY, ZERO_ENTROPY_API_KEY
#     OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_EXPORTER_OTLP_PROTOCOL
#     GUARDRAILS_LOG_FILE_PATH, COLOREDLOGS_LEVEL_STYLES
#
# To sa POSWIADCZENIA i ENDPOINTY, nie przelaczniki zachowania. Nikt ich nie
# ustawia w testach, bo kluczy API sie w testach nie trzyma, a adres kolektora
# telemetrii jest konfiguracja srodowiska. Zgloszenie ich jako "flag, ktorych
# zaden test nie dotyka" jest technicznie prawdziwe i bezuzyteczne — a przy
# okazji topi prawdziwe trafienia w szumie.
#
# Kontrola pyta: "czy istnieje GALAZ KODU, w ktora zaden test nie wchodzi".
# Brakujacy klucz API nie tworzy galezi, tylko wylacza caly integrator.

def test_klucze_api_nie_sa_flagami():
    for nazwa in ("SARVAM_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY",
                  "GITHUB_TOKEN", "DB_PASSWORD", "AWS_SECRET_ACCESS_KEY"):
        assert uf._poswiadczenie_lub_adres(nazwa), nazwa


def test_endpointy_i_adresy_nie_sa_flagami():
    for nazwa in ("OTEL_EXPORTER_OTLP_ENDPOINT", "SARVAM_API_BASE",
                  "GUARDRAILS_LOG_FILE_PATH", "DATABASE_URL",
                  "OTEL_EXPORTER_OTLP_PROTOCOL"):
        assert uf._poswiadczenie_lub_adres(nazwa), nazwa


def test_prawdziwe_flagi_zachowania_ZOSTAJA():
    """Regresja: te, dla ktorych ta kontrola powstala. V7ST_SENTRY_ENFORCE
    bramkowal realna galaz kodu i zaden test go nie ustawial."""
    for nazwa in ("V7ST_SENTRY_ENFORCE", "V7ST_DATAFORGE_CRYPTO",
                  "ENABLE_EXPERIMENTAL", "USE_LEGACY_PARSER", "DEBUG_MODE",
                  "V7ST_ORACLE_FF_OFFSET_HOURS"):
        assert not uf._poswiadczenie_lub_adres(nazwa), nazwa


def test_przestrzenie_chmurowe_sa_konfiguracja_srodowiska():
    """`AWS_ACCESS_KEY_ID` przeszlo przez regule sufiksowa przy pierwszym
    podejsciu, bo konczy sie na `_ID`, a nie `_KEY` — dokladnie ten rodzaj
    chybienia o wlos, ktory lapie regula prefiksowa, a sufiksowa nie moze."""
    for nazwa in ("AWS_ACCESS_KEY_ID", "AWS_REGION", "AWS_PROFILE",
                  "AZURE_TENANT", "GCP_PROJECT"):
        assert uf._poswiadczenie_lub_adres(nazwa), nazwa


def test_prefiks_nie_polyka_flagi_o_podobnej_nazwie():
    """`AWSOME_FEATURE` nie jest przestrzenia AWS — prefiks wymaga separatora."""
    assert not uf._poswiadczenie_lub_adres("AWSOME_FEATURE")
    assert not uf._poswiadczenie_lub_adres("DDOS_PROTECTION_ENABLED")
