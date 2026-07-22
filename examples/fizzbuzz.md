---
# Smoke test: the smallest task that still exercises the whole loop -
# agent run, git commit, validation executed by the runner, fix retry.
validate: test "$(python3 fizzbuzz.py | tr '\n' ' ')" = "1 2 Fizz 4 Buzz Fizz 7 8 Fizz Buzz 11 Fizz 13 14 FizzBuzz "
retry: 1
steps: 8
---

# Task: fizzbuzz.py

## Goal

The project root contains a file `fizzbuzz.py` which, run as
`python3 fizzbuzz.py`, prints the numbers 1 to 15, each on its own line.

## Output rules

- divisible by both 3 and 5 → `FizzBuzz`
- divisible by 3 → `Fizz`
- divisible by 5 → `Buzz`
- otherwise the number itself

## Assertions (definition of done)

- `python3 fizzbuzz.py` prints exactly 15 lines
- the third line is `Fizz`, the fifth `Buzz`, the fifteenth `FizzBuzz`
- the script reads no input and takes no arguments

Do not run the validation command yourself; it runs outside your session.

## Escape hatch

If something is missing or the task contradicts itself, do not improvise:
write a single line starting with `ASSUMPTION FAILED:` and stop.
