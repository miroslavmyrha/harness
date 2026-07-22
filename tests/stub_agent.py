#!/usr/bin/env python3
"""A fake agent.py for exercising the runner without a GPU.

Reads the task file, does what STUB_BEHAVIOUR says, writes a transcript in
the same shape the harness produces (usage counts included) and exits with
the harness's own exit codes. Behaviours:

  ok        - write a passing file
  broken    - write a failing file
  fix       - write a failing file, then a passing one on the fix task
  nothing   - claim success, touch no file
  capped    - write a failing file and exit 2 (hit the step cap)
"""
import json, os, sys, time

task = sys.argv[sys.argv.index("--task") + 1]
root = os.environ["AGENT_ROOT"]
behaviour = os.environ.get("STUB_BEHAVIOUR", "ok")
is_fix = ".fix" in os.path.basename(task)
target = os.path.join(root, "answer.txt")

with open(f"{task}.{time.strftime('%Y%m%d-%H%M%S')}.jsonl", "w") as log:
    log.write(json.dumps({"event": "start", "task_file": task,
                          "system": os.environ.get("AGENT_SYSTEM", "(built-in)")}) + "\n")
    log.write(json.dumps({"role": "assistant", "content": "done",
                          "usage": {"prompt_tokens": 1000, "completion_tokens": 50}}) + "\n")
    log.write(json.dumps({"event": "end", "status": "done"}) + "\n")

if behaviour == "nothing":
    sys.exit(0)
if behaviour == "ok" or (behaviour == "fix" and is_fix):
    open(target, "w").write("42\n")
    sys.exit(0)
with open(target, "w") as f:                       # broken / fix-first / capped
    f.write("nonsense\n")
sys.exit(2 if behaviour == "capped" else 0)
