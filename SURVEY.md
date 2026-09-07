# Survey: 25 widely-used repositories

> **Update 2026-09-07.** Twenty more repositories were scanned since this
> survey — see [SKAN_2026-09-06.md](SKAN_2026-09-06.md) for the batch and
> [SKAN_2026-09-07.md](SKAN_2026-09-07.md) for what the two new checks found.
> **Forty-five in total, and exactly one finding worth reporting**
> ([OpenHands/OpenHands#17148](https://github.com/OpenHands/OpenHands/issues/17148)).
>
> The results below were produced before corrections 7–16. Three of those
> corrections changed answers for repositories in this very table — `astral-sh/uv`
> and `astral-sh/ruff` were rescanned and come back clean, and the tool had been
> blind to `cargo nextest`, which is most of modern Rust. Treat the numbers here
> as the state on 2026-09-04, not as current.

Run on 2026-09-04 with the checks in this repository. The headline is not the
list of findings — it is that **15 of 25 come back completely clean**, and that
most of the remaining ten are describing deliberate configuration rather than a
defect.

That is the result a tool like this should produce. One that finds something
everywhere is finding nothing.

| | |
|---|---|
| repositories scanned | 25 |
| clean | 15 |
| something reported | 10 |
| worth a maintainer's glance, in my reading | 2 |

## Clean

`astral-sh/ruff` · `astral-sh/uv` · `psf/black` · `python-poetry/poetry` ·
`pre-commit/pre-commit` · `encode/httpx` · `fastapi/fastapi` · `tiangolo/typer` ·
`scrapy/scrapy` · `pallets/flask` · `sqlalchemy/sqlalchemy` ·
`Significant-Gravitas/AutoGPT` · `guardrails-ai/guardrails` ·
`NVIDIA-NeMo/Guardrails` · `kenryu42/cc-safety-net`

Two of those are worth a note.

**NVIDIA-NeMo/Guardrails** was reported as untested on Windows and macOS by an
earlier version of this tool. It was wrong: the steps live in `_test.yml` behind
a `uses:` call, and the matrix comment says plainly `# exclude Ubuntu as it is
available in pr-tests`. Resolving local `uses:` targets is now a check the tool
performs, and a test pins it.

**kenryu42/cc-safety-net** was the repository that prompted this tool, and it is
clean as of today. A report on 3 September described its Windows lane running
only `--test-name-pattern '\[windows\]'`; on 4 September the maintainer added
`full-check-windows` and `full-check-macos` jobs running the full `check:ci`.
The tool briefly reported the gap as still open on the day it was closed, because
its runner pattern looked for `bun test` and not `bun run check:ci`. That is
correction five of six.

## Deliberate configuration, reported anyway

These are what the checks are supposed to surface and a human is supposed to
dismiss. Reading them is a two-minute job; that is the intended cost.

| repository | reported | reading |
|---|---|---|
| `openai/openai-agents-python` | `pytest -m requires_native_macos_sandbox` | The job exists to run exactly those tests |
| `langchain-ai/langchain` | `pytest -m compile`; macOS gap | The marker is the point of `compile-integration-tests`; the macOS mention is a dependency-bump workflow |
| `microsoft/autogen` | five `dotnet test --filter "Category=..."` | Standard category split across jobs |
| `BerriAI/litellm` | `pytest -m replayable` | A record-and-replay job, by design |
| `pydantic/pydantic` | `pytest -k 'not test_custom_bson_serializable and not ...'` | Named exclusions of known-failing third-party tests, visible in the command |
| `apache/airflow` | `pytest -m integration_tests`; Windows gap | Marker split; the Windows mention is `test-git-clone-on-windows` |
| `crewAIInc/crewAI` | macOS gap | The mention is `codeql.yml::analyze`, a static analyser |
| `prefecthq/prefect` | `pytest -m windows` | See below |

`prefecthq/prefect` is the closest to a judgement call. Windows coverage has two
gates: `windows-tests.yaml` triggers only on a hand-maintained list of about
twenty paths, and then runs only tests carrying the `windows` marker. The path
list looks carefully chosen — `processutils`, `runner`, `workers`, `flows` — so
this reads as a deliberate scoping of an expensive lane rather than an oversight.
The observation worth making is only that both gates are maintained by hand, so
a new Windows-sensitive module is covered when someone remembers to add it.

## Worth a maintainer's glance

**`microsoft/agent-governance-toolkit`** — 42 workflow files, 98 jobs, none
running tests on Windows or macOS. The single mention of either is the wheel
matrix in `publish.yml`, which builds a `win_amd64` wheel and publishes it. An
issue asking for a Windows runner (#310) was closed as `completed` on
2026-04-01, and its stated acceptance criterion — `windows-latest` in the
`ci.yml` test matrix — is still absent. Reported as
[#3884](https://github.com/microsoft/agent-governance-toolkit/issues/3884).

**`run-llama/llama_index`** — `build_package.yml::build` runs on
`windows-latest`, builds the wheel, installs it and runs `python -c "import
llama_index"`. No job anywhere runs the test suite on Windows. For a pure-Python
package that is a mild instance of the pattern; it would matter more in code
doing path manipulation or subprocess work. Not reported — the smoke import is a
reasonable line to draw, and the maintainers have presumably drawn it.

## Reading the output

Every line this tool prints is a **lead**. The two conditions it detects — a
platform present in CI without tests, and a filtered test lane — are both
perfectly legitimate arrangements. What they have in common is that the default
is silence: nothing turns red when a test is not tagged, or when a new platform
is added to a build matrix without a test lane.

The check tells you where that silence lives. Deciding whether it should be
there is the part a person still has to do.

## Reproducing

```
python ci_claim_gap.py openai/openai-agents-python langchain-ai/langchain ...
```

Add `--json` for machine-readable output. `GH_TOKEN` raises the rate limit; the
scan above is 25 repositories and several hundred workflow files.
