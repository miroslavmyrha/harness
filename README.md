# harness

A minimal agent harness for ollama models — one Python file, stdlib only.
It owns the conversation loop, dispatches tool calls, and enforces safety
rules; the model itself only generates text and tool-call requests.

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
  larger agentic codegen pipeline: the layers above it (task queue, validation,
  git isolation) live outside this repo by design.

Non-goals: multi-agent orchestration, context summarization/compaction,
sandboxing beyond the write jail, parallelism. When those are needed, use a
full-size harness.

## Usage

```
python3 agent.py                   # interactive; asks before bash/file writes [y/N]
python3 agent.py --yolo            # runs everything without asking (except dangerous commands)
python3 agent.py --task task.md    # non-interactive: one task, then exit
```

Quit with `exit`, `quit`, `konec`, `/bye` or Ctrl+D. Ctrl+C during generation
interrupts the current turn, not the whole program.

## Configuration (env)

| Variable           | Meaning                                     | Default                  |
|--------------------|---------------------------------------------|--------------------------|
| `AGENT_MODEL`      | model name in ollama                        | `asistent-agent`         |
| `AGENT_OLLAMA`     | ollama base URL                             | `http://localhost:11434` |
| `AGENT_ROOT`       | directory outside which writes are denied   | `$HOME`                  |
| `AGENT_CTX`        | context window (`num_ctx`, sent per request)| `16384`                  |
| `AGENT_KEEP_ALIVE` | how long the model stays loaded in RAM      | `10m`                    |
| `AGENT_THINK`      | `1`/`true` enables model thinking           | off                      |
| `AGENT_SYSTEM`     | path to a file replacing the system prompt  | built-in prompt          |

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

## Task mode (batch)

`--task file.md` reads the task from the file, runs the tool loop once and
exits — the unit of work for unattended batch runs, where a queue script
above the harness supplies task files and checks results.

- **Transcript**: written next to the task file as `file.md.<timestamp>.jsonl`
  — one JSON object per message, `start`/`end` events, and per-request
  `ctx_used` (context tokens) and `secs` on assistant lines, so a morning
  triage script can see what happened and what each task cost.
- **Exit codes**: `0` finished, `1` error, `2` hit the tool-call cap,
  `3` stopped by the context guard.
- **No stdin, ever**: dangerous commands are auto-denied even with `--yolo`,
  and without `--yolo` every bash/write is denied — a batch can never stall
  overnight on a hidden `[y/N]` prompt. In practice `--task` always pairs
  with `--yolo`.

A task file template lives in [`templates/TASK.md`](templates/TASK.md):
rigid structure with grounding (a verified pattern the model must imitate),
the full current content of target files, small ordered steps, assertions,
an `ASSUMPTION FAILED:` escape hatch, and a `validate:` header meant to be
executed by the queue runner — never by the model itself.

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
- dangerous commands (`sudo`, `rm -rf`, `dd`, `mkfs`, writes to `/dev/`, …)
  require confirmation even in `--yolo` mode; in task mode they are denied
  outright
- cap of 25 tool calls per user input
- context-overflow guard: ollama reports used tokens per request, and at
  ~85 % of `AGENT_CTX` the loop stops with a clear message instead of letting
  ollama silently drop the system prompt and tools
- caveat: `run_bash` is inherently unrestricted (apart from confirmation) —
  the write jail protects against the model's *mistakes*, not against
  unsupervised runs; for real autonomous use run it under a separate user or
  in a container

## Modelfiles

- `Modelfile-agent` — the `asistent-agent` model (default for the agent)
- `Modelfile-asistent` — the `asistent` model (conversational, no tools)

Create with: `ollama create asistent-agent -f Modelfile-agent`

## License

MIT
