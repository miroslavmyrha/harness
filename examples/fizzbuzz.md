---
# Smoke test: the smallest task that still exercises the whole loop -
# agent run, git commit, validation executed by the runner, fix retry.
validate: test "$(python3 fizzbuzz.py | tr '\n' ' ')" = "1 2 Fizz 4 Buzz Fizz 7 8 Fizz Buzz 11 Fizz 13 14 FizzBuzz "
retry: 1
steps: 8
---

# Task: fizzbuzz.py

## Goal

V kořeni projektu existuje soubor `fizzbuzz.py`, který po spuštění
`python3 fizzbuzz.py` vypíše čísla 1 až 15, každé na vlastní řádek.

## Pravidla výpisu

- číslo dělitelné 3 i 5 → `FizzBuzz`
- číslo dělitelné 3 → `Fizz`
- číslo dělitelné 5 → `Buzz`
- jinak samotné číslo

## Assertions (definition of done)

- `python3 fizzbuzz.py` vypíše přesně 15 řádků
- třetí řádek je `Fizz`, pátý `Buzz`, patnáctý `FizzBuzz`
- skript nic nečte ze vstupu a nebere argumenty

Ověřovací příkaz nespouštěj sám, běží mimo tvoji session.

## Escape hatch

Pokud ti něco chybí nebo si zadání odporuje, neimprovizuj: napiš jediný
řádek začínající `ASSUMPTION FAILED:` a skonči.
