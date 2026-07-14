---
# Machine-readable contract for the queue runner. Validation is executed
# deterministically by the runner (or a human) after the agent finishes -
# never by the model itself.
validate: <command that proves the task succeeded, e.g. a linter or test run>
retry: 1
---

# Task: <one-line title>

## Goal

<One sentence. What exists when this task is done.>

## Grounding

Follow **exactly** the patterns shown below. Do not invent other APIs,
helpers, idioms or file layouts, even if you know them from elsewhere.

Verified pattern to imitate:

```
<a small, real, working snippet from the target project - the single
source of truth for style, naming and APIs available to you>
```

## Target files

### <path/to/existing-file> (edit)

Current content:

```
<full current content - the model must not guess what is in the file>
```

### <path/to/new-file> (create)

<what belongs there, in terms of the grounding pattern above>

## Steps

1. <small step>
2. <small step>
3. <small step>

Work through the steps in order. After each file change, re-read the changed
section with read_file to confirm the edit landed as intended.

## Assertions (definition of done)

- <observable fact 1>
- <observable fact 2>

Do NOT run the test suite or linters yourself - validation runs outside
this session. State plainly in your final answer which assertions you
believe hold and why.

## Escape hatch

If anything you need is missing or contradicts this file (a function,
a table, a route), do not improvise a replacement. Write a single line
starting with `ASSUMPTION FAILED:` describing what is missing, and stop.
