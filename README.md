# harness

A minimal agent harness for ollama models and OpenAI-compatible servers —
stdlib only, two files that do one job each:

- **`agent.py`** — the conversation loop. Dispatches tool calls and enforces
  the safety rules; the model itself only generates text and tool-call
  requests.
- **`runner.py`** — batch layer. Runs task files with git isolation and
  executes each task's `validate:` command *outside* the model, because the
  model's own report of success is not evidence.

## Intent

This is a deliberately small, readable harness for driving local (and
remote) open-weight models as agents — reading, writing, and editing files,
running shell commands, and fetching web pages. It exists to:

- serve as a testbed for how much agentic work small local models (12B class)
  can do when the harness gives them tight guardrails and good error messages,
- stay simple enough to understand and modify in one sitting — every safety
  rule is a few visible lines, not a framework,
- be ready to point at bigger models on a remote inference box
  (`AGENT_OLLAMA`) without code changes, as the executor building block of a
  larger agentic codegen pipeline.

`runner.py` is the queue/validation/git-isolation layer that pipeline needs.
It lives here rather than in its own repo for one reason: the contract
between the two files (task front matter, exit codes, transcript naming) is
not something two repos can keep in step silently — see
[Contract between agent.py and runner.py](#contract-between-agentpy-and-runnerpy).
`agent.py` stays free of it: it does not import, know about, or need the
runner.

Non-goals: multi-agent orchestration, LLM-based context summarization (on
context overflow the interactive mode only trims the oldest turns), sandboxing
beyond the write jail, parallelism. When those are needed, use a full-size
harness.

## Usage

```
python3 agent.py                   # interactive; asks before bash/file writes [y/N]
python3 agent.py --yolo            # runs everything without asking (except dangerous commands)
python3 agent.py --task task.md    # non-interactive: one task, then exit

python3 runner.py task.md --project ~/myapp [--playbook symfony]
```

Quit with `exit`, `quit`, `konec`, `/bye` or Ctrl+D. Ctrl+C during generation
interrupts the current turn, not the whole program.

## Configuration (env)

| Variable           | Meaning                                     | Default                  |
|--------------------|---------------------------------------------|--------------------------|
| `AGENT_MODEL`      | model name in ollama, or `provider/model` from opencode.json | `asistent-agent` |
| `AGENT_OLLAMA`     | ollama base URL                             | `http://localhost:11434` |
| `AGENT_ROOT`       | directory outside which writes are denied   | `$HOME`                  |
| `AGENT_CTX`        | context window (`num_ctx`, sent per request)| `16384`                  |
| `AGENT_KEEP_ALIVE` | how long the model stays loaded in RAM      | `10m`                    |
| `AGENT_THINK`      | `1`/`true` enables model thinking           | off                      |
| `AGENT_MAX_STEPS`  | cap on work tool turns (bash/write/edit) per user turn | `25`          |
| `AGENT_MAX_READS`  | separate cap on read-only tool turns (read/list/grep/fetch) | 2× `AGENT_MAX_STEPS` |
| `AGENT_SYSTEM`     | path to a file replacing the system prompt  | built-in prompt          |
| `AGENT_DEBUG_RAW`  | file path: appends every raw SSE `data:` line from an OpenAI-compatible server (opencode.json mode) to it, for debugging malformed streamed tool calls | off |

The `asistent-agent` default only exists on the machine the Modelfiles were
built for — elsewhere just point `AGENT_MODEL` at any tool-calling model.
Size the context to the machine: `AGENT_CTX` is sent per request and a bigger
value costs RAM (raising it triggers a one-off model reload).

`AGENT_SYSTEM` is the hook for adapting the harness to different
applications: put a per-project playbook (coding idioms, allowed APIs,
output format) into a file outside this repo and point the variable at it;
the task itself goes into `--task`.

Example with a remote machine:

```
AGENT_OLLAMA=http://192.168.1.50:11434 AGENT_MODEL=gemma4:31b python3 agent.py
```

### OpenAI-compatible endpoints (opencode.json)

If `AGENT_MODEL` contains a slash, it is looked up as `provider/model` in
`~/.config/opencode/opencode.json` (the [opencode](https://opencode.ai)
config) and the harness talks to that provider's OpenAI-compatible
`/chat/completions` endpoint instead of ollama — vLLM, llama-server, or
anything else speaking the OpenAI protocol:

```
AGENT_MODEL=home-qwen/qwen3.6-27b python3 agent.py
```

The provider's `options.baseURL` and optional `options.apiKey` are used as
is; the model's `limit.context` becomes the default `AGENT_CTX` (an explicit
`AGENT_CTX` still wins) and `limit.output` is sent as `max_tokens`.
`AGENT_THINK` is forwarded as `chat_template_kwargs.enable_thinking`;
`AGENT_OLLAMA` and `AGENT_KEEP_ALIVE` do not apply in this mode.

## Task mode (batch)

`--task file.md` reads the task from the file, runs the tool loop once and
exits — the unit of work for unattended batch runs. `runner.py` is what
supplies those task files and checks the results.

- **Transcript**: written next to the task file as `file.md.<timestamp>.jsonl`
  — one JSON object per message, `start`/`end` events, and per-request
  `ctx_used` (context tokens), `usage` (exact prompt/completion token counts)
  and `secs` on assistant lines, so a morning triage script can see what
  happened and what each task cost.
- **Exit codes**: `0` finished, `1` error, `2` hit the tool-call cap,
  `3` stopped by the context guard.
- **No stdin, ever**: commands matching the dangerous-pattern list are
  auto-denied even with `--yolo`, and without `--yolo` every bash/write is
  denied — a batch can never stall overnight on a hidden `[y/N]` prompt. In
  practice `--task` always pairs with `--yolo`; read the caveat under
  [Safety rails](#safety-rails) before leaving one unattended.

A task file template lives in [`templates/TASK.md`](templates/TASK.md):
rigid structure with grounding (a verified pattern the model must imitate),
the full current content of target files, small ordered steps, assertions,
an `ASSUMPTION FAILED:` escape hatch, and a `validate:` header executed by
`runner.py` — never by the model itself.

## Batch runs (`runner.py`)

```
python3 runner.py TASK.md [TASK2.md …] --project ~/myapp [--playbook symfony]
```

Six benchmark rounds produced one consistent result: **not a single bug was
ever caught by the model's own final answer.** Every one was found by a
`curl`, a puppeteer run or a compiler. So the runner never asks the model
how it went. One task run is:

1. **Parse the front matter** — `validate:`, `retry:`, `timeout:`, `steps:`, `reads:`
2. **Isolate in git** — refuse a dirty tree, branch `task/<name>-<time>` from
   HEAD, so the baseline is always a rollback point and the task's diff is
   always separable from your own work
3. **Run `agent.py`** with `AGENT_ROOT` *and cwd* set to the project, the
   playbook in `AGENT_SYSTEM`, and the caps from the front matter
4. **Commit whatever appeared.** An empty diff is a verdict, not a no-op:
   `NO CHANGES` means the model claimed success and touched nothing
5. **Run `validate:`** in the project dir, with a timeout. Exit 0 is the only
   thing that counts as a pass
6. **On failure, retry once per `retry:`** with a generated fix task: the real
   symptom (the validate output), the cause placed on the model, and
   `edit_file`-only instructions — full rewrites during fixes have silently
   deleted working code
7. **Append one JSON line** to `runs.jsonl`: verdict, branch, commits, exact
   token counts summed from the transcript, seconds, diffstat

Verdicts: `PASS`, `FAIL`, `NO CHANGES`, `UNVERIFIED` (no `validate:` header —
the task ran, but nothing proves anything, and it never shows up as green).
A failed run is left on its branch with the inspect/discard commands printed;
nothing is discarded automatically.

Front matter is four scalar keys, deliberately not YAML — a missing PyYAML
must never be why a night's queue did not run. Keep task files **outside**
the project repo, otherwise they dirty the tree they are supposed to measure.
See [`examples/fizzbuzz.md`](examples/fizzbuzz.md).

| flag | meaning |
|---|---|
| `--project DIR` | target repo, becomes `AGENT_ROOT` and cwd (default `.`) |
| `--playbook X` | name from `~/agent-playbooks/build` or a path → `AGENT_SYSTEM` |
| `--model X` | `AGENT_MODEL` override |
| `--steps N` | default step cap when the task does not set one |
| `--log PATH` | run log (default `runs.jsonl` beside `runner.py`) |
| `--allow-dirty` | run despite uncommitted changes — they are parked in their own `pre-task WIP` commit first, so they are never attributed to the agent |
| `--keep-going` | continue the queue after a failed task |
| `--dry-run` | parse and report, run nothing |

Exit code is 0 only if every task passed. Sequential only; parallel tasks
would need one worktree each. Only `validate:` is time-bounded — a wedged
agent run blocks the queue.

### Contract between `agent.py` and `runner.py`

The runner drives the agent as a subprocess, so it depends on six details.
They are listed here because a silent drift between them does not crash
anything — it produces confident, wrong verdicts.

| # | The runner relies on | Defined in |
|---|---|---|
| 1 | `agent.py` sitting next to `runner.py` | `HARNESS` in `runner.py` |
| 2 | the `--yolo --task <file>` CLI | `agent.py` argv parsing |
| 3 | exit codes `0/1/2/3` = done / error / cap / context | `run_task()` |
| 4 | transcript at `<task>.<timestamp>.jsonl` | `run_task()` |
| 5 | `usage.prompt_tokens` / `usage.completion_tokens` per assistant line | `run_turn()` |
| 6 | the `AGENT_*` environment variable names | top of `agent.py` |

Changing any of them means changing both files in the same commit.

### Tests

```
bash tests/test_runner.sh
```

Thirteen assertions driving the whole loop against `tests/stub_agent.py`, a
fake agent with scripted behaviours (`ok`, `broken`, `fix`, `nothing`,
`capped`) swapped in via `TASK_RUNNER_AGENT` — the plumbing is proven without
spending GPU time.

## Tools

- `run_bash` — shell command, 120 s timeout, output truncated to 4000 chars
- `read_file` — reads ~6000 chars at a time, continue via `from_line`, 5 MB cap
- `write_file` — writes a whole file (new files / full rewrite)
- `edit_file` — exact string replacement (`old_string` must be unique), optional `replace_all`
- `grep` — recursive regex search (`file:line: text`), optional `glob` filter,
  skips hidden dirs / `node_modules` / binaries, capped at 100 matches
- `list_dir` — directory listing
- `web_fetch` — fetches a page, extracts link titles + cleaned text (no JS)

Every truncation is explicit: `run_bash`, `read_file`, and `grep` end cut
output with a visible `[truncated …]` marker and a hint how to continue, so
the model never mistakes a partial result for a complete one.

## Safety rails

- writes (`write_file`, `edit_file`) only inside `AGENT_ROOT`; symlinks are
  resolved via `realpath`
- a dangerous-pattern list (`sudo`, `rm -rf`, `dd`, `mkfs`, writes to
  `/dev/`, …) requires confirmation even in `--yolo` mode, and in task mode
  denies outright. Treat it as a guard against the model's slips, not as a
  boundary: it is a regex over the command string, and near-synonyms walk
  straight past it — `rm --recursive --force`, `find … -delete`,
  `git clean -fdx`, `python3 -c "shutil.rmtree(…)"` are all allowed
- two tool-turn budgets per user input: work turns (bash/write/edit, default
  25 via `AGENT_MAX_STEPS`) and read-only turns (read/list/grep/fetch, default
  2× that via `AGENT_MAX_READS`) — exploration can't eat the work budget,
  but can't loop forever either
- context-overflow guard: the server reports used tokens per request, and at
  ~85 % of `AGENT_CTX` the loop stops with a clear message instead of letting
  ollama silently drop the system prompt and tools
- caveat: `run_bash` and `read_file` are unrestricted (apart from
  confirmation) — the write jail protects against the model's *mistakes*,
  not against unsupervised runs. Anything your user can read, the model can
  read and ship out through the same shell, and text arriving via `web_fetch`
  reaches the model with that shell still attached. For real autonomous use
  run it under a separate user or in a container.

What that adds up to, checked by test rather than by reading the code:

- the write jail **holds** — `..` traversal and a symlinked directory inside
  `AGENT_ROOT` were both refused, the file outside stayed untouched
- the dangerous-pattern list **does not** — `rm -rf /path` is stopped, while
  `rm --recursive --force /path`, `find … -delete`, `git clean -fdx`,
  `python3 -c "shutil.rmtree(…)"` and `curl -F data=@~/.ssh/id_rsa …` all
  pass. It is a guard against slips; do not treat it as a boundary
- `runner.py`'s git isolation sees only what lands **inside the project**;
  a file written elsewhere through the shell is invisible to it. Running the
  agent with cwd set to the project makes that unlikely, not impossible

The fix for the gap is placement — a separate user, a container — not a
longer regex.

## Modelfiles

- `Modelfile-agent` — the `asistent-agent` model (default for the agent)
- `Modelfile-asistent` — the `asistent` model (conversational, no tools)

Create with: `ollama create asistent-agent -f Modelfile-agent`

## License

MIT
