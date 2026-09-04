"""Tests for mock_only_tests.

The distinction this check has to get right: heavy mocking is not the problem.
Mocking everything and then never looking at the result is. A test that patches
five collaborators and asserts on the returned value is a good test.

Run: python -m pytest test_mock_only_tests.py -q
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import mock_only_tests as m


def _zbadaj(kod: str, tmp_path: Path):
    p = tmp_path / "test_probka.py"
    p.write_text(textwrap.dedent(kod), encoding="utf-8")
    return m.zbadaj_plik(p)


# --- przypadek zrodlowy ------------------------------------------------------

def test_lapie_test_patrzacy_tylko_na_okablowanie(tmp_path):
    """Ksztalt z guardrails-ai #1633: obie implementacje zamockowane, asercja
    mowi tylko, ktora zostala wywolana."""
    w = _zbadaj("""
        def test_validate_with_sync(mocker):
            mocker.patch("mod.should_run_sync", return_value=True)
            mocker.patch("mod.SequentialValidatorService")
            mocker.patch("mod.AsyncValidatorService")
            vs.validate(value=True)
            vs.SequentialValidatorService.assert_called_once_with(True)
    """, tmp_path)
    assert len(w) == 1
    assert w[0].test == "test_validate_with_sync"
    assert w[0].ile_patchy == 3


# --- czego NIE wolno zglaszac ------------------------------------------------

def test_duzo_atrap_ale_asercja_na_wartosci_jest_w_porzadku(tmp_path):
    """To jest dobry test. Mockowanie nie jest wada - slepota na wynik jest."""
    w = _zbadaj("""
        def test_zwraca_polaczony_wynik(mocker):
            mocker.patch("mod.a")
            mocker.patch("mod.b")
            mocker.patch("mod.c")
            wynik = polacz()
            assert wynik == "oczekiwane"
    """, tmp_path)
    assert w == []


def test_jedna_atrapa_to_za_malo(tmp_path):
    """Pojedyncza atrapa z asercja wywolania to zwykly test interakcji,
    nie objaw dyspozytora sprawdzanego po okablowaniu."""
    w = _zbadaj("""
        def test_powiadamia(mocker):
            mocker.patch("mod.wyslij")
            zrob()
            mod.wyslij.assert_called_once()
    """, tmp_path)
    assert w == []


def test_atrapy_bez_asercji_wywolania_nie_sa_zglaszane(tmp_path):
    """Atrapy jako samo rusztowanie - test moze byc pusty albo niedokonczony,
    ale to inna wada niz ta, ktorej szukamy."""
    w = _zbadaj("""
        def test_nic_nie_sprawdza(mocker):
            mocker.patch("mod.a")
            mocker.patch("mod.b")
            zrob()
    """, tmp_path)
    assert w == []


def test_funkcja_bez_prefiksu_test_jest_pomijana(tmp_path):
    w = _zbadaj("""
        def pomocnik(mocker):
            mocker.patch("mod.a")
            mocker.patch("mod.b")
            mod.a.assert_called_once()
    """, tmp_path)
    assert w == []


# --- warianty skladni --------------------------------------------------------

def test_rozpoznaje_monkeypatch_setattr(tmp_path):
    w = _zbadaj("""
        def test_dyspozytor(monkeypatch):
            monkeypatch.setattr(mod, "a", atrapa)
            monkeypatch.setattr(mod, "b", atrapa2)
            zrob()
            atrapa.assert_called_once_with(1)
    """, tmp_path)
    assert len(w) == 1


def test_rozpoznaje_test_asynchroniczny(tmp_path):
    w = _zbadaj("""
        async def test_asynchroniczny(mocker):
            mocker.patch("mod.a")
            mocker.patch("mod.b")
            await zrob()
            mod.a.assert_awaited_once()
    """, tmp_path)
    assert len(w) == 1


def test_asercja_wywolania_wewnatrz_assert_nie_liczy_sie_jako_wartosc(tmp_path):
    """`assert x.assert_called()` to nadal asercja wywolania, nie wartosci -
    inaczej dalo by sie ukryc problem jednym slowem kluczowym."""
    w = _zbadaj("""
        def test_ukryte(mocker):
            mocker.patch("mod.a")
            mocker.patch("mod.b")
            zrob()
            assert mod.a.assert_called_once_with(1)
    """, tmp_path)
    assert len(w) == 1


# --- odpornosc ---------------------------------------------------------------

def test_plik_z_bledem_skladni_nie_wywraca_skanera(tmp_path):
    p = tmp_path / "test_zly.py"
    p.write_text("def test_(:\n    pass\n", encoding="utf-8")
    assert m.zbadaj_plik(p) == []


def test_prog_atrap_jest_konfigurowalny(tmp_path):
    kod = """
        def test_jedna(mocker):
            mocker.patch("mod.a")
            zrob()
            mod.a.assert_called_once()
    """
    p = tmp_path / "test_probka.py"
    p.write_text(textwrap.dedent(kod), encoding="utf-8")
    assert m.zbadaj_plik(p, min_patchy=2) == []
    assert len(m.zbadaj_plik(p, min_patchy=1)) == 1
