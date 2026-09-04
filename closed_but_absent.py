"""Find issues closed as completed whose stated criterion is absent from the code.

A closed issue is a claim that something was done. When the issue names a file
and a thing that should be in it, the claim is checkable in one request.

This came from `microsoft/agent-governance-toolkit` #310, "test: add Windows
compatibility tests to CI", closed as `completed` on 2026-04-01. Its first
acceptance criterion read:

    Add `windows-latest` to the test matrix in `.github/workflows/ci.yml`

Five months later `ci.yml` contained no occurrence of `windows-latest`, and in
the interim a bug was filed describing exactly the failure a Windows runner is
meant to catch. The issue had no comments, no linked pull request and no
referencing commit.

The check: for each issue closed as completed, find sentences that name a
backticked path and a backticked token, then look for that token in that file on
the default branch. Report the pairs where the token is missing.

Reports leads, not verdicts. The criterion may have been met a different way,
the file may have been renamed, or the team may have consciously changed their
mind - all legitimate, and none of them visible from here. Read the issue.

    python closed_but_absent.py owner/repo [--limit 60]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = "https://api.github.com"

# `path/to/file.ext` - a backticked path with an extension we can fetch.
SCIEZKA = re.compile(r"`([\w./\-]+\.(?:ya?ml|toml|cfg|ini|json|py|ts|js|tsx|jsx|md|sh|txt))`")
# Any other backticked token in the same sentence, treated as the thing to look for.
TOKEN = re.compile(r"`([^`\n]{2,60})`")

# Tokens too generic to be evidence of anything.
NIEISTOTNE = {"true", "false", "null", "none", "yes", "no", "on", "off"}

# A sentence that prescribes rather than describes. Without this the check
# reports "`hooks/hooks.json` runs `npx -y cc-safety-net`" - a statement about
# what the environment does, where nobody ever promised the token would be in
# the file.
PRZEPIS = re.compile(
    r"^\s*(?:[-*]\s*)?(?:we\s+(?:should|must|need\s+to)\s+|please\s+)?"
    r"(add|set|use|enable|include|register|define|introduce|create|move|rename|"
    r"replace|switch|configure|declare|expose|export|wire|pin|bump)\b",
    re.I,
)


@dataclass
class Para:
    numer: int
    tytul: str
    zamkniete: str
    plik: str
    token: str
    zdanie: str


def _pobierz(url: str, token: str | None):
    zad = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "closed-but-absent",
        **({"Authorization": f"Bearer {token}"} if token else {}),
    })
    with urllib.request.urlopen(zad, timeout=30) as odp:
        return json.load(odp)


def _plik_z_repo(repo: str, sciezka: str, galaz: str) -> str | None:
    url = f"https://raw.githubusercontent.com/{repo}/{galaz}/{urllib.parse.quote(sciezka)}"
    zad = urllib.request.Request(url, headers={"User-Agent": "closed-but-absent"})
    try:
        with urllib.request.urlopen(zad, timeout=30) as odp:
            return odp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return None


def _jest_kryterium(zdanie: str) -> bool:
    """Czy to zdanie MOWI CO ZROBIC, czy tylko opisuje stan.

    Pierwsza wersja tej funkcji nie istniala i skrypt zglosil trzy zdania
    z cc-safety-net w rodzaju "`hooks/hooks.json` runs `npx -y cc-safety-net`".
    To sa OPISY srodowiska, a nie kryteria - token nie ma prawa byc w pliku,
    bo nikt nigdy nie obiecal, ze tam bedzie.

    Microsoft #310 przeszedl, bo brzmial rozkazujaco: "Add `windows-latest` to
    the test matrix in `.github/workflows/ci.yml`".
    """
    z = zdanie.strip()
    if re.match(r"^[-*]\s*\[[ xX]\]", z):          # pozycja listy kontrolnej
        return True
    return bool(PRZEPIS.search(z))


def _pary_z_tresci(tresc: str) -> list[tuple[str, str, str]]:
    """Zdania, ktore PRZEPISUJA plik i token. Zwraca (plik, token, zdanie)."""
    wynik: list[tuple[str, str, str]] = []
    for zdanie in re.split(r"(?<=[.;:\n])\s+", tresc or ""):
        pliki = SCIEZKA.findall(zdanie)
        if not pliki:
            continue
        if not _jest_kryterium(zdanie):
            continue
        for plik in pliki:
            for tok in TOKEN.findall(zdanie):
                if tok == plik or tok.lower() in NIEISTOTNE:
                    continue
                if SCIEZKA.fullmatch(f"`{tok}`"):
                    continue          # to inna sciezka, nie token do szukania
                wynik.append((plik, tok, zdanie.strip()[:180]))
    return wynik


def zbadaj(repo: str, limit: int, token: str | None) -> list[Para]:
    try:
        info = _pobierz(f"{API}/repos/{repo}", token)
        galaz = info.get("default_branch", "main")
        issues = _pobierz(
            f"{API}/repos/{repo}/issues?state=closed&per_page={min(limit,100)}&sort=updated",
            token,
        )
    except urllib.error.HTTPError as e:
        print(f"{repo}: HTTP {e.code}")
        return []

    cache: dict[str, str | None] = {}
    braki: list[Para] = []

    for i in issues:
        if "pull_request" in i or i.get("state_reason") != "completed":
            continue
        for plik, tok, zdanie in _pary_z_tresci(i.get("body") or ""):
            if plik not in cache:
                cache[plik] = _plik_z_repo(repo, plik, galaz)
            tresc = cache[plik]
            if tresc is None:
                continue                      # pliku nie ma - inna historia
            if tok not in tresc:
                braki.append(Para(
                    numer=i["number"], tytul=i["title"][:80],
                    zamkniete=(i.get("closed_at") or "")[:10],
                    plik=plik, token=tok, zdanie=zdanie,
                ))
    return braki


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="owner/name")
    ap.add_argument("--limit", type=int, default=60, help="ile zamknietych issues obejrzec")
    a = ap.parse_args()

    braki = zbadaj(a.repo, a.limit, os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"))

    if not braki:
        print("nic nie znalazlem")
        return 0

    print(f"kryteriow nieobecnych w kodzie: {len(braki)}\n")
    for b in braki:
        print(f"#{b.numer}  zamkniete {b.zamkniete}  {b.tytul}")
        print(f"   szukane: `{b.token}`   w: {b.plik}   -> BRAK")
        print(f"   z tresci: {b.zdanie}")
        print()

    print("To sa TROPY. Kryterium mogło zostac spelnione inaczej, plik zmieniony,")
    print("albo zespol swiadomie zmienil zdanie. Przeczytaj issue przed zgloszeniem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
