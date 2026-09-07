# ci-claim-gap

Finds four specific gaps between what a repository's CI appears to cover and
what it actually runs.

**1. Platform gap** — an OS appears in CI (a build matrix, a wheel target, a
runner) but no job anywhere in the repository runs a test suite on it. The
project ships for that platform and never tests there.

**2. Opt-in coverage** — a test step exists but is filtered by name or marker,
so a test only reaches that lane if somebody remembered to tag it. Nothing fails
when the tagging step is skipped, so coverage silently decays as tests are added.

**3. Gated test step** — the job's only test step carries an `if:` that can
switch it off, so the job passes under a name containing "test" while running
none of them.

**4. Swallowed failure** — the test step runs but cannot fail the job
(`|| true`, `continue-on-error: true`), so the green tick carries no
information about whether the suite passed.

All four share one shape: **a mechanism that has to be remembered rather than
enforced.** Nothing breaks when someone forgets; the signal just quietly stops
meaning anything.

```
python ci_claim_gap.py owner/repo [owner/repo ...]
```

Read-only. Public GitHub API. No cloning. `GH_TOKEN` optional but raises the
rate limit.

## Example

```
$ python ci_claim_gap.py kenryu42/cc-safety-net

kenryu42/cc-safety-net
  workflows: 8   jobs: 13   delegating: 0
  tests run on: ['windows']
  PLATFORM GAP - the OS appears in CI, but no job anywhere tests on it:
    macos  (appears in: ci.yml::packed-runtime)
  OPT-IN COVERAGE - the test step is filtered, so it covers only tagged tests:
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

## Sixteen corrections, each now a test

This tool gave a wrong answer sixteen times before it gave a useful one. Each
mistake is pinned by a test in `test_ci_claim_gap.py`, so the suite is a record
of what it got wrong rather than a restatement of what the code does.

| It reported | Reality | Fix |
|---|---|---|
| "No Windows testing" after reading `ci.yml` | A separate `test-windows.yml` existed | Enumerate every workflow file, never one |
| `python -m pytest` as a marker filter | `-m` belonged to `python`, not `pytest` | A filter flag only counts after the tool name |
| NVIDIA NeMo-Guardrails as untested on Windows and macOS | Steps live in `_test.yml` behind a `uses:` call | Resolve local `uses:` targets before judging |
| FastAPI and Typer as untested on Windows and macOS | Their test step is `bash scripts/test-cov.sh` | Recognise script and `make` targets as test runners |
| cc-safety-net's platform gap as still open, on the day it was closed | The new jobs call `bun run check:ci` | Recognise package scripts as test runners |
| microsoft/autogen as untested on Windows, in a job named `test-autogen-ext-pwsh` | Its step is `poe ... test-windows` | Recognise task runners with named tasks |
| vLLM as untested on macOS | Its step is `- name: Smoke test vllm serve`, running `vllm serve` | Read the step NAME, not only the command — but accept `test` there, never `check`, or every `Check out repository` becomes a test suite |
| browser-use as untested on Windows and macOS | `- name: Set up venv and test for OS/Python versions`, on a three-OS matrix | Same fix; two repositories hit the same blind spot on the same day |
| astral-sh/uv's `Install NASM` step, which runs no test at all, as a gated test step | The task-runner pattern used a negated character class, which matches newlines: `Invoke-WebRequest` on one line joined the word `checksum` three lines below | Exclude the newline from every such class — the same flaw was in all three filter patterns |
| Ten gated steps in OpenHands, of which one was real | `if: always()` is the *opposite* of a gate, and `Upload test artifacts` handles a test's output rather than running it | A gate counts only when the step's COMMAND runs tests, and never for `always()` / `success()` / `!cancelled()` |
| continuedev/continue as having a switchable test step | `Run smoke tests` and `Run tests` are unconditional there; only `Run e2e tests` is gated off Windows, which is narrower coverage, not absent coverage | A gate counts only when EVERY test step in the job is gated |
| zed as testing on Linux only, with gaps on Windows and macOS | It runs `run_tests_windows`, `run_tests_linux` and `run_tests_mac`, each calling `cargo nextest run` — the default Rust test runner, which `\btest\b` cannot match inside "nextest" | Recognise `cargo nextest run`; the pattern had been blind to most of modern Rust |
| langfuse as having a macOS platform gap | macOS appears in exactly one place across its 31 workflows: `codeql.yml::analyze`. CodeQL takes a macOS runner to analyse Swift | Static analysis is neither a build nor a shipment, so it cannot raise the accusation — though it still counts as evidence NOT to accuse |
| withastro/astro as having a switchable test step | It pairs `if: runner.os == 'Linux'` with `if: runner.os != 'Linux'` — the same complementary shape as correction 8c, written with an operator instead of a leading `!` | Fold `!=` onto `==` when comparing conditions; the compared value survives, so `== windows` and `!= macos` stay distinct |
| dbt-core and mlflow as swallowing test failures | Both run tests to MEASURE them — dbt's `Run integration tests and store durations` sits in a workflow called "Update Test Durations", mlflow's in a job called `set-matrix`. `|| true` is correct there: you want the timing even from a test that failed | Exclude jobs whose purpose is measurement, matched on workflow OR job name |
| zed's macOS gap, still, after that fix | Its runner is `namespace-profile-mac-large`. Large projects rarely use `macos-latest` | Accept `mac` followed by a separator — and *only* by a separator, or `machine` and `macro` register as Macs, which this correction's own test caught |

The last three matter most, and the last one is this tool marking its own
homework: the gated-step check produced nine false hits on its own first run,
against the very repository it was written for. A tool that accuses a project of
skipping tests when that project is doing it correctly is worse than no tool,
because it costs the reader the one thing it was supposed to save: trust in the
report.

Measured honestly: on a batch of twelve fresh repositories scanned on
2026-09-06, the platform-gap check produced **three false positives out of four
hits** before these corrections — and all three were the same shape, "a test I
do not recognise as a test". That number is in
[SKAN_2026-09-06.md](SKAN_2026-09-06.md) rather than rounded away.

A platform is therefore only reported as a gap when **no job in the repository**
tests on it. A build-only job is not an accusation when another workflow covers
the same OS.

### A limit that pattern-matching cannot fix

`check` is ambiguous, and the ambiguity is in English rather than in the regex.
It means "quality gate" in `check.sh` and "test a condition" in
`check_discussion.py` — an issue-triage script in `continuedev/continue` that
this tool still reports. Dropping `check` from the filename pattern would clear
that hit and simultaneously stop the tool recognising every genuine `check.sh`,
which was correction 4. It is left in, and this paragraph is the honest
disclosure rather than a silently tuned threshold.

Validated against 25 widely-used repositories — see [SURVEY.md](SURVEY.md).
Fifteen come back completely clean, and most of the remaining ten are describing
deliberate configuration rather than a defect. That is the result that matters:
a tool that finds something everywhere is finding nothing.

## Swallowed failures: the test runs, fails, and CI passes anyway

Reported by the same command. A step that runs tests but cannot fail the job —
`|| true`, `|| echo`, or `continue-on-error: true` — turns the green tick into
noise: it carries no information about whether the suite passed.

`withastro/astro` runs

```yaml
- name: Test ts-plugin (Linux, flaky)
  run: xvfb-run -a pnpm test || echo "::warning ...Known flaky; not failing CI."
```

They documented it, which is the honest way to do it — but the shape is worth
finding, because most of the time nobody writes that comment.

Restricted to steps that actually run tests: `|| true` while cleaning up or
uploading an artefact is ordinary practice and says nothing about coverage.

## Gated test steps: the job goes green and runs nothing

Reported alongside platform gaps by the same command. A step that runs tests but
carries an `if:` condition can be switched off on one lane while the job keeps
its name and its green tick.

`OpenHands/OpenHands` (86k stars) runs `test-and-build` on
`[ubuntu-24.04, windows-latest]`, and its `Lint`, `Test`, `Build library` and
`Verify package contents` steps all carry `if: matrix.full_checks`. The Windows
lane sets `full_checks: false`. So `test-and-build (windows)` passes on every
pull request while running `npm ci` and `npm run build` and not one test — and
the project ships a Windows `.exe` and a macOS `.dmg` built by workflows that
only build. Reported as
[#17148](https://github.com/OpenHands/OpenHands/issues/17148).

The check deliberately ignores `always()`, `success()` and `!cancelled()`: those
force a step to run rather than skip it, so reporting them as gates would be
exactly backwards. It also requires the step's command to run tests, because
`Upload test artifacts` and `Render test report` handle a suite's output and say
nothing about whether it ran.

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

`assert_not_called()` counts as looking at behaviour, not at wiring. A test
proving that nothing happened — no signal sent, no fallback taken — is testing
the thing itself when the function's product is a side effect. Only
`assert_called_once_with(...)` and friends are wiring, because a test can
satisfy those while two implementations disagree about what they return.

### One reason a hit can be safe that the script cannot see

`run-llama/llama_index` has `test_acquire_blocks_when_exhausted`, whose only
assertion is `mock_sleep.assert_called_once()` — no argument, so on the face of
it the test cannot tell a one-second wait from a microsecond one. Falsifying it
by hand says otherwise: the test also sets

```python
mock_time.side_effect = [base, base + 2.0]
```

and a `side_effect` list is a **hidden assertion on the number of iterations**.
Break the wait calculation and the loop asks the clock a third time, so the test
fails on `StopIteration` before its own assertion is ever reached.

That is not something a static reader can be expected to spot, and it is exactly
why the output says leads rather than verdicts. Checking this one cost twenty
minutes and prevented a low-quality report against a large project — which is
the cheaper half of that trade.

## A third check: issues closed as completed whose criterion is absent

```
python closed_but_absent.py owner/repo [--limit 60]
```

A closed issue is a claim that something was done. When the issue names a file
and a thing that should be in it, the claim is checkable in one request.

From `microsoft/agent-governance-toolkit` #310, "test: add Windows compatibility
tests to CI", closed as `completed` on 2026-04-01:

> Add `windows-latest` to the test matrix in `.github/workflows/ci.yml`

Five months later that file contained no occurrence of `windows-latest`, and in
the interim a bug was filed describing exactly the failure a Windows runner is
meant to catch. The issue had no comments, no linked pull request and no
referencing commit.

The check reads sentences that **prescribe** rather than describe. That
distinction is the whole check: its first version reported three sentences from
another repository of the form "`hooks/hooks.json` runs `npx -y cc-safety-net`",
which state what the environment does rather than promise what a file will
contain. A sentence now qualifies only if it is a checklist item or opens with a
prescriptive verb.

## A fourth check: flags that gate behaviour and no test ever sets

```
python untested_flags.py path/to/package --tests path/to/tests
```

Finds environment variables a module branches on that are never mentioned
anywhere in the test suite. The read has tests, the branch it guards has tests,
but no test ever sets the variable — so only one of the two paths is ever
exercised. The switch is one nobody has flipped.

This generalises a failure from the author's own system: a `SENTRY_ENFORCE=1`
flag with a working reader, passing tests, and logs reporting "protection
active, 0 violations" every week. Every word of that was true and meaningless,
because no process ever wrote the table the reader read.

You cannot detect a missing writer from static text. You can detect the weaker
and still useful thing: a flag whose second state the suite has never entered.

Pointed at that same system today, it reports 20 flags read and 5 the tests
never mention. Pointed at this repository, it reports none.

Operating-system and CI variables — `APPDATA`, `HOME`, `GITHUB_TOKEN`,
`RUNNER_TEMP` and the rest — are excluded. `APPDATA` was this check's first
false positive.

## Using it as a GitHub Action

```yaml
- uses: tomek89wo-ops/ci-claim-gap@main
  with:
    tests-path: tests            # optional, enables the wiring-only check
    check-closed-issues: "true"  # optional, needs GH_TOKEN
```

Results land in the job summary. It does not fail the build by default: these
are leads, and a red build is the wrong way to deliver a lead. Set
`fail-on-findings: "true"` if you would rather gate on them.

## Why these checks

All of them come from the same shape: a mechanism that has to be remembered rather
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
