# harness

Minimální agent harness nad ollama (~300 řádků Pythonu, bez závislostí mimo stdlib).
Drží konverzační smyčku, dispatchuje tool-cally a vynucuje bezpečnostní pravidla;
model jen generuje text a požadavky na nástroje.

## Použití

```
python3 agent.py          # ptá se před bash/zápisem [y/N]
python3 agent.py --yolo   # spouští vše bez ptaní (kromě nebezpečných příkazů)
```

Konec: `exit`, `quit`, `konec`, `/bye` nebo Ctrl+D. Ctrl+C během generování
přeruší aktuální tah, ne celý program.

## Konfigurace (env)

| Proměnná       | Význam                              | Výchozí                  |
|----------------|-------------------------------------|--------------------------|
| `AGENT_MODEL`  | název modelu v ollama               | `asistent-agent`         |
| `AGENT_OLLAMA` | základ URL ollamy                   | `http://localhost:11434` |
| `AGENT_ROOT`   | složka, mimo kterou je zákaz zápisu | `$HOME`                  |

Příklad se vzdáleným strojem:

```
AGENT_OLLAMA=http://192.168.1.50:11434 AGENT_MODEL=gemma4-27b python3 agent.py
```

## Nástroje

- `run_bash` — shell příkaz, timeout 120 s, výstup ořezán na 4000 znaků
- `read_file` — čtení po ~6000 znacích, pokračování přes `from_line`, strop 5 MB
- `write_file` — zápis celého souboru (nové soubory / úplný přepis)
- `edit_file` — přesná náhrada řetězce (`old_string` musí být jednoznačný), volitelně `replace_all`
- `list_dir` — výpis složky
- `web_fetch` — stažení stránky, extrakce titulků odkazů + očištěný text (bez JS)

## Bezpečnostní pojistky

- zápis (`write_file`, `edit_file`) jen uvnitř `AGENT_ROOT`, symlinky se rozbalují přes `realpath`
- nebezpečné příkazy (`sudo`, `rm -rf`, `dd`, `mkfs`, zápis do `/dev/`, …) chtějí
  potvrzení **i v `--yolo` režimu**
- strop 25 tool-callů na jeden uživatelský vstup
- pozor: `run_bash` je z principu neomezený (kromě potvrzování) — jail na zápisy
  chrání proti *omylům* modelu, ne proti běhu bez dozoru; pro ostrý autonomní
  provoz spouštěj pod odděleným uživatelem nebo v kontejneru

## Modelfily

- `Modelfile-agent` — model `asistent-agent` (výchozí pro agenta)
- `Modelfile-asistent` — model `asistent` (konverzační, bez nástrojů)

Vytvoření: `ollama create asistent-agent -f Modelfile-agent`
