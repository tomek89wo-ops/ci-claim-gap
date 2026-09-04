# ci-claim-gap

Finds two specific gaps between what a repository's CI appears to cover and what
it actually runs.

**1. Platform gap** — an OS appears in CI (a build matrix, a wheel target, a
runner) but no job anywhere in the repository runs a test suite on it. The
project ships for that platform and never tests there.

**2. Opt-in coverage** — a test step exists but is filtered by name or marker,
so a test only reaches that lane if somebody remembered to tag it. Nothing fails
when the tagging step is skipped, so coverage silently decays as tests are added.

```
python ci_claim_gap.py owner/repo [owner/repo ...]
```

Read-only. Public GitHub API. No cloning. `GH_TOKEN` optional but raises the
rate limit.

## Example

```
$ python ci_claim_gap.py kenryu42/cc-safety-net

kenryu42/cc-safety-net
  workflowow: 8   zadan: 13   delegujacych: 0
  testy chodza na: ['windows']
  LUKA PLATFORMY - system wystepuje w CI, ale NIGDZIE nie ma tam testow:
    macos  (wspomniany w: ci.yml::packed-runtime)
  POKRYCIE OPT-IN - krok testowy filtrowany, obejmuje tylko oznaczone:
    test-windows.yml :: test-windows (windows)  [--test-name-pattern]
      run: bun test tests --test-name-pattern '\[windows\]'
```

Windows is tested, but only for tests whose names carry a `[windows]` tag —
46 of 5142 in that repository at the time of writing. macOS appears in the
packaging matrix and is never tested.

## What it is not

It reports **leads, not verdicts.** A filter can be deliberate. Tests can run
somewhere this script cannot see — a self-hosted runner, a separate CI system, a
scheduled workflow in another repository. Read the workflow file and the issue
tracker before you report anything to anyone.

That warning is not boilerplate. It exists because the author reported a missing
Windows job in a public repository after reading only `ci.yml`, and was wrong.

## Three corrections, each now a test

This tool gave a wrong answer three times before it gave a useful one. Each
mistake is pinned by a test in `test_ci_claim_gap.py`, so the suite is a record
of what it got wrong rather than a restatement of what the code does.

| It reported | Reality | Fix |
|---|---|---|
| "No Windows testing" after reading `ci.yml` | A separate `test-windows.yml` existed | Enumerate every workflow file, never one |
| `python -m pytest` as a marker filter | `-m` belonged to `python`, not `pytest` | A filter flag only counts after the tool name |
| NVIDIA NeMo-Guardrails as untested on Windows and macOS | Steps live in `_test.yml` behind a `uses:` call | Resolve local `uses:` targets before judging |
| FastAPI and Typer as untested on Windows and macOS | Their test step is `bash scripts/test-cov.sh` | Recognise script and `make` targets as test runners |

The last two matter most. A tool that accuses a project of skipping tests
when that project is doing it correctly is worse than no tool, because it costs
the reader the one thing it was supposed to save: trust in the report.

A platform is therefore only reported as a gap when **no job in the repository**
tests on it. A build-only job is not an accusation when another workflow covers
the same OS.

Validated against roughly twenty widely-used repositories. ruff, uv, black,
poetry, pre-commit, scrapy, FastAPI, Typer and NeMo-Guardrails all come back
clean, which is the result that matters: a tool that finds something everywhere
is finding nothing.

## A second check: tests that assert wiring instead of behaviour

```
python mock_only_tests.py path/to/tests
```

Finds Python tests that patch out two or more collaborators, call the thing
under test, and then assert only *which* collaborator was called — never what
came back. Such a test passes identically whether or not the collaborators agree
on their output, so a divergence between them is invisible to it by
construction.

This came from a real report. `guardrails-ai/guardrails` has an open issue
(#1633) where the sync and async validator services return different validated
output and the default path silently discards fixes. Pointing this script at the
dispatcher tests for that code:

```
  :72   test_validate_with_sync   (mocks: 5, call assertions: 2, value assertions: 0)
  :97   test_validate_with_async   (mocks: 5, call assertions: 2, value assertions: 0)
  :122  test_validate_with_no_available_event_loop   (mocks: 5, call assertions: 3, value assertions: 0)
```

Heavy mocking is not the finding. Mocking everything and never looking at the
result is. Run against this repository's own tests — which use
`monkeypatch.setattr` seven times across tests with two or more patches — it
reports nothing, because every one of them asserts on a value.

## Why these checks

All three come from the same shape: a mechanism that has to be remembered rather
than enforced. A Windows runner that builds but does not test, and a test lane that
only runs tagged tests, are both arrangements where the default is silence.
Nothing goes red when the step is forgotten — coverage just quietly stops
growing, and the next platform-specific bug reaches a user instead of a CI log.

## Running the tests

```
python -m pytest test_ci_claim_gap.py -q
```

No dependencies beyond the standard library and pytest.

## Licence

MIT
