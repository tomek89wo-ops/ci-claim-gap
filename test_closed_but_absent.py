"""Tests for closed_but_absent.

The distinction this check has to get right is between a sentence that
prescribes and a sentence that describes. "Add `x` to `f.yml`" is a promise that
`x` will be in `f.yml`. "`f.yml` runs `x`" is a statement about the world, and
nobody promised anything.

Getting that wrong is not a rounding error: the first version of this script
reported three descriptions from cc-safety-net as unmet criteria.

Run: python -m pytest test_closed_but_absent.py -q
"""

from __future__ import annotations

import closed_but_absent as c


# --- przypadek zrodlowy ------------------------------------------------------

def test_lapie_kryterium_z_microsoft_310():
    """Doslowne zdanie z issue #310, ktore zamknieto jako completed."""
    tresc = "1. Add `windows-latest` to the test matrix in `.github/workflows/ci.yml`"
    pary = [(p, t) for p, t, _ in c._pary_z_tresci(tresc)]
    assert (".github/workflows/ci.yml", "windows-latest") in pary


def test_lapie_pozycje_listy_kontrolnej():
    tresc = "- [ ] Add `pytest-xdist` to `pyproject.toml`"
    pary = c._pary_z_tresci(tresc)
    assert [(p, t) for p, t, _ in pary] == [("pyproject.toml", "pytest-xdist")]


# --- korekta: opis to nie kryterium -----------------------------------------

def test_opis_srodowiska_nie_jest_kryterium():
    """Z cc-safety-net #80. Zdanie mowi, co plik ROBI - nie obiecuje niczego."""
    tresc = ("The marketplace plugin cache contains `safety-net/0.4.0/`, "
             "while `hooks/hooks.json` runs `npx -y cc-safety-net`.")
    assert c._pary_z_tresci(tresc) == []


def test_zdanie_o_wersji_nie_jest_kryterium():
    """Z cc-safety-net #72 - opis zainstalowanej wersji, nie zadanie."""
    tresc = ("cc-safety-net **2.0.3**, installed via the marketplace into "
             "`~/.claude/plugins/cache/foo/2.0.3/` with `hooks/hooks.json`.")
    assert c._pary_z_tresci(tresc) == []


def test_rozpoznaje_rozne_czasowniki_przepisujace():
    for czasownik in ("Add", "Set", "Enable", "Register", "Pin", "Configure"):
        tresc = f"{czasownik} `some-token` in `config.toml`"
        assert c._pary_z_tresci(tresc), f"nie rozpoznano: {czasownik}"


def test_we_should_tez_sie_liczy():
    assert c._pary_z_tresci("We should add `foo` to `setup.cfg`")


# --- higiena tokenow ---------------------------------------------------------

def test_pomija_tokeny_bez_tresci():
    assert c._pary_z_tresci("Set `true` in `config.yml`") == []


def test_druga_sciezka_nie_jest_szukanym_tokenem():
    """`a.yml` i `b.yml` w jednym zdaniu - zadna nie jest tokenem do szukania
    w tej drugiej."""
    pary = c._pary_z_tresci("Move `old.yml` to `new.yml`")
    assert all(not t.endswith(".yml") for _, t, _ in pary)


def test_zdanie_bez_sciezki_jest_pomijane():
    assert c._pary_z_tresci("Add `windows-latest` to the matrix") == []


# --- odpornosc ---------------------------------------------------------------

def test_pusta_tresc_nie_wywraca():
    assert c._pary_z_tresci("") == []
    assert c._pary_z_tresci(None) == []


def test_jest_kryterium_rozroznia_wprost():
    assert c._jest_kryterium("Add `x` to `f.yml`")
    assert c._jest_kryterium("- [x] Set `x` in `f.yml`")
    assert not c._jest_kryterium("`f.yml` runs `x`")
    assert not c._jest_kryterium("The cache contains `x`")
