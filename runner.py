#!/usr/bin/env python3
"""runner.py - the deterministic layer above agent.py.

The harness runs one task and stops; whether the task actually succeeded was
until now decided by a human reading the code. Every bug found in the
benchmark series was caught by curl/puppeteer, never by the model's own
final answer - so the model's word is not evidence and the `validate:`
header in templates/TASK.md was never executed by anything.

This runner closes that loop: task front matter in, git-isolated agent run,
validate command executed here (never by the model), one fix attempt per
retry with the real failure output as the symptom, one line per run in the
log. It does not touch agent.py and has no dependencies beyond git.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.realpath(__file__))
# TASK_RUNNER_AGENT swaps in a stub agent; the tests drive the whole loop
# without spending an hour of GPU time to prove the plumbing works.
HARNESS = os.environ.get("TASK_RUNNER_AGENT", os.path.join(HERE, "agent.py"))
PLAYBOOKS = os.path.expanduser("~/agent-playbooks/build")
C = {"red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
     "dim": "\033[2m", "bold": "\033[1m", "reset": "\033[0m"}


def say(msg, color=None):
    print(f"{C.get(color, '')}{msg}{C['reset']}", flush=True)


# ---------------------------------------------------------------- task file

def parse_task(path):
    """Split a task file into front-matter meta and the prompt body.

    Front matter is the block between a leading '---' and the next '---',
    'key: value' per line, '#' comments ignored. Deliberately not YAML: the
    contract is four scalar keys and a missing PyYAML must never be the
    reason a night's queue does not run."""
    with open(path) as f:
        text = f.read()
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                say(f"  ignoring unparsable front-matter line: {line}", "yellow")
                continue
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    known = {"validate", "retry", "timeout", "steps", "reads"}
    for k in set(meta) - known:
        say(f"  unknown front-matter key ignored: {k}", "yellow")
    return meta, body.strip()


def intval(meta, key, default):
    try:
        return int(meta.get(key, default))
    except ValueError:
        say(f"  {key}: {meta[key]!r} is not a number, using {default}", "yellow")
        return default


# ---------------------------------------------------------------------- git

def git(project, *args, check=True):
    r = subprocess.run(["git", "-C", project, *args],
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def git_ready(project, allow_dirty):
    """Refuse to start unless the project is a git repo with a clean tree.

    Isolation is the whole point: without a known-good baseline commit a bad
    run cannot be told apart from the state that preceded it, and 'what did
    this task actually change' has no answer."""
    if not os.path.isdir(os.path.join(project, ".git")):
        sys.exit(f"{project} is not a git repository (git init first) - "
                 f"refusing to run without a rollback point.")
    dirty = git(project, "status", "--porcelain")
    if dirty and not allow_dirty:
        sys.exit(f"{project} has uncommitted changes:\n{dirty}\n"
                 f"Commit or stash them (or pass --allow-dirty) - otherwise "
                 f"the task's diff cannot be separated from your own.")
    return git(project, "rev-parse", "HEAD")


def slug(path):
    return re.sub(r"[^a-z0-9]+", "-", os.path.basename(path).lower()).strip("-")[:40]


# -------------------------------------------------------------- agent + eval

def run_agent(task_path, project, env_extra, model, playbook, steps, reads):
    """One agent.py invocation. Returns (exit_code, transcript_path, tokens).

    The agent is handed the task *body* only, never the front matter.
    `validate:` in particular is the acceptance test - handing it over turns
    "implement the spec" into "satisfy this exact command", a different and
    much easier task. Observed for real: given the path, a model read the
    check script before writing a line of code. Keeping the script outside
    the project does not help - `read_file` is not confined to `AGENT_ROOT` -
    but not naming it does."""
    _, body = parse_task(task_path)
    env = {**os.environ, "AGENT_ROOT": project, **env_extra}
    if model:
        env["AGENT_MODEL"] = model
    if playbook:
        env["AGENT_SYSTEM"] = playbook
    env["AGENT_MAX_STEPS"] = str(steps)
    env["AGENT_MAX_READS"] = str(reads)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        prompt = os.path.join(tmp, os.path.basename(task_path))
        with open(prompt, "w") as f:
            f.write(body + "\n")
        # cwd=project is not cosmetic: AGENT_ROOT only guards write_file/edit_file,
        # while relative paths and every run_bash command resolve against the
        # process cwd. Launched from anywhere else, a model writing "app/x.py"
        # lands outside the project - seen on the first real run of this runner.
        r = subprocess.run([sys.executable, HARNESS, "--yolo", "--task", prompt],
                           env=env, cwd=project)
        # The agent writes its transcript next to the prompt it was given;
        # move it back beside the real task file, keeping the timestamp name.
        produced = sorted(glob.glob(prompt + ".*.jsonl"))
        transcript = None
        if produced:
            stamp = os.path.basename(produced[-1]).rsplit(".", 2)[-2]
            transcript = f"{task_path}.{stamp}.jsonl"
            shutil.move(produced[-1], transcript)
    return r.returncode, transcript, tokens_of(transcript), round(time.time() - t0)


def tokens_of(transcript):
    """Sum exact prompt+completion counts the harness logs per request."""
    if not transcript:
        return 0
    total = 0
    with open(transcript) as f:
        for line in f:
            try:
                usage = (json.loads(line).get("usage") or {})
            except ValueError:
                continue
            total += (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
    return total


AGENT_STATUS = {0: "done", 1: "error", 2: "hit step cap", 3: "context guard"}


def validate(cmd, project, timeout):
    """Run the validate command here, in the runner, never in the model's
    session. Returns (ok, combined_output)."""
    try:
        r = subprocess.run(cmd, shell=True, cwd=project, capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"validate command exceeded {timeout}s and was killed"
    out = (r.stdout + r.stderr).strip()
    return r.returncode == 0, out or f"(no output, exit code {r.returncode})"


def commit_attempt(project, label):
    """Commit whatever the agent produced. An empty diff is a finding, not a
    no-op: the model reporting success while no file changed is a failure
    mode this series has seen for real."""
    git(project, "add", "-A")
    if not git(project, "status", "--porcelain"):
        return None
    git(project, "commit", "-q", "-m", label)
    return git(project, "rev-parse", "--short", "HEAD")


# ------------------------------------------------------------------ fix task

FIX_TEMPLATE = """---
retry: 0
---

# Task: fix the failing check

## Symptom

An acceptance check ran outside your session, against the running app, and
failed. Its output:

```
{output}
```

## What to do

The cause is in the code you just wrote, not in the check and not in the
environment. Read the failing lines above as the symptom, find the cause,
fix it with the **smallest possible change**, and rewrite nothing else.

- Change files **through `edit_file` only**. Do not use `write_file` on an
  existing file here - during fixes, rewriting a whole file has repeatedly
  deleted code that worked.
- After each change, read the changed section back with `read_file` and
  confirm the edit landed as you intended.
- Do not run or modify the validation command; it runs outside your session.

## Escape hatch

If the cause lies outside your code, or something you need for the fix is
missing, do not improvise a replacement: write a single line starting with
`ASSUMPTION FAILED:` describing what is missing, and stop.

## Original task (context)

{original}
"""


def write_fix_task(task_path, attempt, output, original):
    # Neither the validate command nor its path is written into the fix task:
    # when validation is a script, the command *is* the path to the test the
    # model must not read. Only the failing output - the symptom - goes in.
    path = f"{task_path}.fix{attempt}.md"
    with open(path, "w") as f:
        f.write(FIX_TEMPLATE.format(output=output[-3000:], original=original))
    return path


# ------------------------------------------------------------------ one task

def run_one(task_path, args, log):
    meta, body = parse_task(task_path)
    cmd = meta.get("validate")
    retries = intval(meta, "retry", 0)
    timeout = intval(meta, "timeout", 600)
    steps = intval(meta, "steps", args.steps)
    reads = intval(meta, "reads", 2 * steps)
    project = os.path.realpath(args.project)

    say(f"\n{'=' * 70}\n{os.path.basename(task_path)}\n{'=' * 70}", "bold")
    if not cmd:
        say("  no validate: header - this task cannot be verified, only run.",
            "yellow")
    if args.dry_run:
        say(f"  project={project} validate={cmd!r} retry={retries} "
            f"steps={steps} playbook={args.playbook}")
        return "dry-run"

    baseline = git_ready(project, args.allow_dirty)
    origin_ref = git(project, "rev-parse", "--abbrev-ref", "HEAD")
    branch = f"task/{slug(task_path)}-{time.strftime('%H%M%S')}"
    git(project, "checkout", "-q", "-b", branch)
    say(f"  branch {branch} from {baseline[:7]} (was on {origin_ref})", "dim")
    # --allow-dirty used to hand the agent's commit your uncommitted work as
    # well: `git add -A` cannot tell the two apart, so the WIP showed up in
    # the task's diff as if the model had written it. Park it in its own
    # commit first and move the baseline past it - what follows is the
    # agent's, and only the agent's.
    if git(project, "status", "--porcelain"):
        git(project, "add", "-A")
        git(project, "commit", "-q", "-m", "pre-task WIP (yours, not the agent's)")
        baseline = git(project, "rev-parse", "HEAD")
        say(f"  parked your uncommitted changes in {baseline[:7]} on this branch",
            "yellow")

    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "task": task_path,
              "project": project, "branch": branch, "baseline": baseline[:7],
              "playbook": args.playbook or "(built-in)", "validate": cmd,
              "attempts": [], "tokens": 0, "secs": 0}
    verdict, current, original = "?", task_path, body

    for attempt in range(retries + 2):        # initial run + one per retry
        code, transcript, tokens, secs = run_agent(
            current, project, {}, args.model, args.playbook, steps, reads)  # current: task path
        record["tokens"] += tokens
        record["secs"] += secs
        status = AGENT_STATUS.get(code, f"exit {code}")
        sha = commit_attempt(project, f"{os.path.basename(current)} (attempt {attempt})")
        att = {"task": os.path.basename(current), "agent": status,
               "commit": sha, "tokens": tokens, "secs": secs}
        say(f"  agent: {status}, {tokens:,} tokens, {secs}s, commit {sha or '-'}",
            "dim")

        if sha is None:
            att["verdict"] = verdict = "NO CHANGES"
            say("  FAIL: the agent changed nothing - its answer is not evidence.",
                "red")
            record["attempts"].append(att)
            break
        if not cmd:
            att["verdict"] = verdict = "UNVERIFIED"
            record["attempts"].append(att)
            break

        ok, out = validate(cmd, project, timeout)
        att["validate"] = "PASS" if ok else "FAIL"
        att["validate_output"] = out[-2000:]
        record["attempts"].append(att)
        if ok:
            verdict = "PASS"
            say(f"  validate PASS: {cmd}", "green")
            break
        say(f"  validate FAIL: {cmd}\n{out[-1500:]}", "red")
        verdict = "FAIL"
        if attempt >= retries:
            break
        current = write_fix_task(task_path, attempt + 1, out, original)
        say(f"  retrying with fix task {os.path.basename(current)}", "yellow")

    record["verdict"] = verdict
    record["diff"] = git(project, "diff", "--shortstat", f"{baseline}..HEAD") or "(none)"
    log.write(json.dumps(record, ensure_ascii=False) + "\n")
    log.flush()

    color = {"PASS": "green", "UNVERIFIED": "yellow"}.get(verdict, "red")
    say(f"  {verdict}  {record['diff']}  {record['tokens']:,} tokens", color)
    if verdict != "PASS":
        say(f"  inspect: git -C {project} diff {baseline[:7]}..{branch}\n"
            f"  discard: git -C {project} checkout {origin_ref} && "
            f"git -C {project} branch -D {branch}", "dim")
    return verdict


def main():
    p = argparse.ArgumentParser(
        description="Run harness tasks with git isolation and deterministic validation.")
    p.add_argument("tasks", nargs="+", help="task files, run in order")
    p.add_argument("--project", default=".", help="target repo (AGENT_ROOT)")
    p.add_argument("--playbook", help="AGENT_SYSTEM: a name from ~/agent-playbooks/build or a path")
    p.add_argument("--model", help="AGENT_MODEL override")
    p.add_argument("--steps", type=int, default=25, help="default AGENT_MAX_STEPS")
    p.add_argument("--log", default=os.path.join(HERE, "runs.jsonl"))
    p.add_argument("--allow-dirty", action="store_true", help="run even with uncommitted changes")
    p.add_argument("--keep-going", action="store_true", help="continue the queue after a failed task")
    p.add_argument("--dry-run", action="store_true", help="parse and report, run nothing")
    args = p.parse_args()

    if args.playbook and not os.path.exists(args.playbook):
        candidate = os.path.join(PLAYBOOKS, args.playbook + ".md")
        if not os.path.exists(candidate):
            sys.exit(f"playbook not found: {args.playbook} (nor {candidate}) - "
                     f"build it with ~/agent-playbooks/mk.sh")
        args.playbook = candidate
    if args.playbook:
        args.playbook = os.path.realpath(args.playbook)
    for t in args.tasks:
        if not os.path.exists(t):
            sys.exit(f"task file not found: {t}")

    results = []
    with open(args.log, "a") as log:
        for t in args.tasks:
            v = run_one(os.path.realpath(t), args, log)
            results.append((t, v))
            if v not in ("PASS", "dry-run") and not args.keep_going:
                say(f"\nstopping: {os.path.basename(t)} did not pass "
                    f"(--keep-going to continue anyway)", "red")
                break

    say(f"\n{'=' * 70}", "bold")
    for t, v in results:
        say(f"  {v:<12} {os.path.basename(t)}",
            {"PASS": "green", "UNVERIFIED": "yellow", "dry-run": "dim"}.get(v, "red"))
    sys.exit(0 if all(v in ("PASS", "dry-run") for _, v in results) else 1)


if __name__ == "__main__":
    main()
