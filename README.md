# harness

A minimal agent harness for ollama models (~300 lines of Python, stdlib only).
It owns the conversation loop, dispatches tool calls, and enforces safety
rules; the model itself only generates text and tool-call requests.

## Intent

This is a deliberately small, readable harness for driving local (and later
remote) open-weight models as agents — reading, writing, and editing files,
running shell commands, and fetching web pages. It exists to:

- serve as a testbed for how much agentic work small local models (12B class)
  can do when the harness gives them tight guardrails and good error messages,
- stay simple enough to understand and modify in one sitting — every safety
  rule is a few visible lines, not a framework,
- be ready to point at bigger models on a remote inference box
  (`AGENT_OLLAMA`) without code changes, as a building block for a larger
  agentic codegen pipeline.

Non-goals: multi-agent orchestration, context summarization, sandboxing
beyond the write jail. When those are needed, use a full-size harness.

## Usage

```
python3 agent.py          # asks before bash/file writes [y/N]
python3 agent.py --yolo   # runs everything without asking (except dangerous commands)
```

Quit with `exit`, `quit`, `konec`, `/bye` or Ctrl+D. Ctrl+C during generation
interrupts the current turn, not the whole program.

## Configuration (env)

| Variable       | Meaning                                  | Default                  |
|----------------|------------------------------------------|--------------------------|
| `AGENT_MODEL`  | model name in ollama                     | `asistent-agent`         |
| `AGENT_OLLAMA` | ollama base URL                          | `http://localhost:11434` |
| `AGENT_ROOT`   | directory outside which writes are denied| `$HOME`                  |

Example with a remote machine:

```
AGENT_OLLAMA=http://192.168.1.50:11434 AGENT_MODEL=gemma4:31b python3 agent.py
```

## Tools

- `run_bash` — shell command, 120 s timeout, output truncated to 4000 chars
- `read_file` — reads ~6000 chars at a time, continue via `from_line`, 5 MB cap
- `write_file` — writes a whole file (new files / full rewrite)
- `edit_file` — exact string replacement (`old_string` must be unique), optional `replace_all`
- `grep` — recursive regex search (`file:line: text`), optional `glob` filter,
  skips hidden dirs / `node_modules` / binaries, capped at 100 matches
- `list_dir` — directory listing
- `web_fetch` — fetches a page, extracts link titles + cleaned text (no JS)

## Safety rails

- writes (`write_file`, `edit_file`) only inside `AGENT_ROOT`; symlinks are
  resolved via `realpath`
- dangerous commands (`sudo`, `rm -rf`, `dd`, `mkfs`, writes to `/dev/`, …)
  require confirmation **even in `--yolo` mode**
- cap of 25 tool calls per user input
- caveat: `run_bash` is inherently unrestricted (apart from confirmation) —
  the write jail protects against the model's *mistakes*, not against
  unsupervised runs; for real autonomous use run it under a separate user or
  in a container

## Modelfiles

- `Modelfile-agent` — the `asistent-agent` model (default for the agent)
- `Modelfile-asistent` — the `asistent` model (conversational, no tools)

Create with: `ollama create asistent-agent -f Modelfile-agent`
