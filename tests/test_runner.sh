#!/usr/bin/env bash
# End-to-end test of the runner loop against tests/stub_agent.py.
# Every case asserts the verdict written to the run log, not just stdout.
set -uo pipefail
cd "$(dirname "$0")"
HERE=$(pwd)
RUNNER=$HERE/../runner.py
export TASK_RUNNER_AGENT=$HERE/stub_agent.py
fails=0

setup() {                       # fresh throwaway project + task dir beside it
    WORK=$(mktemp -d)           # the project repo
    TASKS=$(mktemp -d)          # task files live outside it, as in ~/benchmarks
    LOG=$TASKS/runs.jsonl
    git -C "$WORK" init -q
    git -C "$WORK" commit -q --allow-empty -m base
    cat > "$TASKS/task.md" <<EOF
---
validate: test "\$(cat answer.txt)" = "42"
retry: ${1:-0}
---
# Task: write the answer
EOF
}

check() {                       # check <case> <expected verdict>
    got=$(python3 -c "import json,sys
print(json.loads(open(sys.argv[1]).read().strip().split(chr(10))[-1])['verdict'])" "$LOG" 2>/dev/null)
    if [ "$got" = "$2" ]; then
        echo "PASS  $1 -> $got"
    else
        echo "FAIL  $1 -> expected $2, got ${got:-<no log line>}"
        fails=$((fails + 1))
    fi
}

run() { python3 "$RUNNER" "$TASKS/task.md" --project "$WORK" --log "$LOG" "${@:2}" >"$TASKS/out.$1" 2>&1; }

# 1. happy path
setup 0; STUB_BEHAVIOUR=ok      run happy;    check "passing task" PASS
# 2. validation actually fails the run (the whole point)
setup 0; STUB_BEHAVIOUR=broken  run broken;   check "failing validate" FAIL
# 3. fix loop: first attempt fails, retry passes
setup 1; STUB_BEHAVIOUR=fix     run fix;      check "fix loop recovers" PASS
grep -q "fix the failing check" "$TASKS"/task.md.fix1.md \
    && echo "PASS  fix task written from template" \
    || { echo "FAIL  fix task missing"; fails=$((fails + 1)); }
grep -qE "validate:|answer.txt" "$TASKS"/task.md.fix1.md \
    && { echo "FAIL  fix task leaks the validate command"; fails=$((fails + 1)); } \
    || echo "PASS  fix task carries the symptom, not the validate command"
# 4. model claims success, writes nothing
setup 0; STUB_BEHAVIOUR=nothing run nothing;  check "empty diff caught" "NO CHANGES"
# 5. agent hits the step cap -> still validated, still fails
setup 0; STUB_BEHAVIOUR=capped  run capped;   check "step cap" FAIL
grep -q "hit step cap" "$LOG" && echo "PASS  agent status recorded" \
    || { echo "FAIL  agent status missing"; fails=$((fails + 1)); }
# 6. no validate: header -> honest UNVERIFIED, not a green PASS
setup 0; printf -- '---\nretry: 0\n---\n# Task: unverifiable\n' > "$TASKS/task.md"
STUB_BEHAVIOUR=ok run noval; check "missing validate header" UNVERIFIED
# 7. dirty tree is refused
setup 0; echo dirt > "$WORK/dirt.txt"
STUB_BEHAVIOUR=ok run dirty
grep -q "uncommitted changes" "$TASKS/out.dirty" && echo "PASS  dirty tree refused" \
    || { echo "FAIL  dirty tree not refused"; fails=$((fails + 1)); }
# 8. baseline commit is never touched
setup 0; before=$(git -C "$WORK" rev-parse HEAD)
STUB_BEHAVIOUR=broken run isolation
after=$(git -C "$WORK" rev-parse master 2>/dev/null || git -C "$WORK" rev-parse main)
[ "$before" = "$after" ] && echo "PASS  base branch untouched" \
    || { echo "FAIL  base branch moved"; fails=$((fails + 1)); }
# 9b. --allow-dirty must not attribute your WIP to the agent
setup 0; echo "my work in progress" > "$WORK/wip.txt"
STUB_BEHAVIOUR=ok run wip --allow-dirty
git -C "$WORK" show --stat --format= HEAD | grep -q wip.txt \
    && { echo "FAIL  WIP landed in the agent's commit"; fails=$((fails + 1)); } \
    || echo "PASS  WIP kept out of the agent's commit"
git -C "$WORK" log --oneline | grep -q "pre-task WIP" \
    && echo "PASS  WIP preserved in its own commit" \
    || { echo "FAIL  WIP lost"; fails=$((fails + 1)); }
# 9c. the validate command / front matter must never reach the model
setup 0
cat > "$TASKS/task.md" <<'EOF'
---
validate: bash /secret/checks/SENTINEL_VALIDATE.sh
retry: 0
---
# Task: SENTINEL_BODY write the answer
EOF
STUB_BEHAVIOUR=ok run leak
sent=$(cat "$TASKS"/task.md.*.jsonl | python3 -c "import json,sys
recv=''.join(json.loads(l).get('content','') for l in sys.stdin
             if l.strip() and json.loads(l).get('role')=='user')
print('BODY' if 'SENTINEL_BODY' in recv else 'no-body',
      'LEAK' if ('SENTINEL_VALIDATE' in recv or 'validate:' in recv) else 'clean')")
[ "$sent" = "BODY clean" ] \
    && echo "PASS  model gets the body, not the validate command ($sent)" \
    || { echo "FAIL  leak check: $sent"; fails=$((fails + 1)); }
# 9. token accounting reaches the log
setup 0; STUB_BEHAVIOUR=ok run tokens
python3 -c "import json,sys
r=json.loads(open(sys.argv[1]).read().strip().split(chr(10))[-1])
sys.exit(0 if r['tokens']==1050 else 1)" "$LOG" \
    && echo "PASS  tokens summed from transcript" \
    || { echo "FAIL  token accounting"; fails=$((fails + 1)); }

echo
[ $fails -eq 0 ] && echo "all green" || echo "$fails failing"
exit $fails
