#!/usr/bin/env python3
"""Minimalni lokalni agent nad ollama (model asistent-agent) s tool-callingem.
Pouziti:  python3 ~/agent.py          (ptá se pred bash/zapisem)
          python3 ~/agent.py --yolo   (spousti vse bez ptani)
Env:      AGENT_MODEL  - nazev modelu (vychozi asistent-agent)
          AGENT_OLLAMA - zaklad URL ollamy (vychozi http://localhost:11434)
"""
import json, os, sys, subprocess, urllib.request, gzip, zlib, re, html

OLLAMA = os.environ.get("AGENT_OLLAMA", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("AGENT_MODEL", "asistent-agent")
YOLO = "--yolo" in sys.argv
MAX_STEPS = 25  # strop volani nastroju na jeden tah

# barvy
C = dict(reset="\033[0m", dim="\033[2m", cyan="\033[36m",
         yellow="\033[33m", green="\033[32m", red="\033[31m", bold="\033[1m")

SYSTEM = ("Jsi strucny agent na lokalnim pocitaci. Mas nastroje run_bash, "
          "read_file, write_file, edit_file, list_dir, web_fetch. Kdyz je potreba, "
          "zavolej nastroj; jinak odpovez cesky. Pracuj po malych krocich. "
          "Existujici soubory upravuj pres edit_file (presna nahrada retezce), "
          "write_file pouzivej jen na nove soubory nebo uplny prepis.")

TOOLS = [
    {"type": "function", "function": {"name": "run_bash",
        "description": "Spusti shell prikaz a vrati jeho vystup.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "prikaz k spusteni"}},
            "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Precte obsah souboru (max ~6000 znaku najednou; u delsich "
                       "souboru pokracuj parametrem from_line).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "from_line": {"type": "integer", "description": "cist od tohoto radku (vychozi 1)"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file",
        "description": "Zapise (prepise) cely obsah souboru. Pro upravy "
                       "existujicich souboru pouzij radeji edit_file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Upravi soubor nahradou presneho retezce. old_string musi "
                       "v souboru existovat presne (vcetne odsazeni a novych radku) "
                       "a byt jednoznacny, jinak pridej vic okolniho kontextu.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "presny text k nahrazeni"},
            "new_string": {"type": "string", "description": "novy text"},
            "replace_all": {"type": "boolean", "description": "nahradit vsechny vyskyty (vychozi false)"}},
            "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "list_dir",
        "description": "Vypise obsah slozky.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "vychozi '.'"}},
            "required": []}}},
    {"type": "function", "function": {"name": "web_fetch",
        "description": "Stahne webovou stranku a vrati titulky odkazu + ocisteny text. "
                       "Pozn.: nefunguje na strankach vykreslovanych JavaScriptem.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "adresa stranky"}},
            "required": ["url"]}}},
]


def confirm(action):
    if YOLO:
        return True
    ans = input(f"{C['yellow']}  povolit {action}? [y/N] {C['reset']}").strip().lower()
    return ans in ("y", "yes", "a", "ano")


def tool_run_bash(args):
    cmd = args.get("command", "")
    print(f"{C['dim']}  $ {cmd}{C['reset']}")
    if not confirm(f"prikaz: {cmd}"):
        return "ODMITNUTO uzivatelem."
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:4000] if out else f"(bez vystupu, navratovy kod {r.returncode})"
    except subprocess.TimeoutExpired:
        return "CHYBA: prikaz prekrocil 120s timeout."
    except Exception as e:
        return f"CHYBA: {e}"


def tool_read_file(args):
    try:
        start = max(int(args.get("from_line") or 1), 1)
    except (TypeError, ValueError):
        start = 1
    try:
        with open(args["path"], "r", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"CHYBA: {e}"
    if start > len(lines):
        return f"CHYBA: soubor ma jen {len(lines)} radku."
    out, size = [], 0
    for i, line in enumerate(lines[start - 1:], start):
        if size + len(line) > 6000 and out:
            out.append(f"\n[zkraceno - soubor ma {len(lines)} radku, "
                       f"pokracuj s from_line={i}]")
            break
        out.append(line)
        size += len(line)
    return "".join(out) or "(prazdny soubor)"


def tool_write_file(args):
    path, content = args.get("path", ""), args.get("content", "")
    print(f"{C['dim']}  zapis -> {path} ({len(content)} znaku){C['reset']}")
    if not confirm(f"zapis do {path}"):
        return "ODMITNUTO uzivatelem."
    try:
        with open(path, "w") as f:
            f.write(content)
        return f"OK, zapsano {len(content)} znaku do {path}."
    except Exception as e:
        return f"CHYBA: {e}"


def tool_edit_file(args):
    path = args.get("path", "")
    old, new = args.get("old_string", ""), args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not old:
        return "CHYBA: old_string nesmi byt prazdny."
    if old == new:
        return "CHYBA: old_string a new_string jsou stejne."
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return f"CHYBA: {e}"
    n = text.count(old)
    if n == 0:
        return ("CHYBA: old_string v souboru nenalezen - musi sedet presne "
                "vcetne odsazeni a novych radku.")
    if n > 1 and not replace_all:
        return (f"CHYBA: old_string nalezen {n}x, musi byt jednoznacny - "
                "pridej vic okolniho kontextu, nebo pouzij replace_all=true.")
    count = n if replace_all else 1
    print(f"{C['dim']}  edit -> {path} ({count}x nahrada, "
          f"-{len(old)}/+{len(new)} znaku){C['reset']}")
    if not confirm(f"editace {path}"):
        return "ODMITNUTO uzivatelem."
    try:
        with open(path, "w") as f:
            f.write(text.replace(old, new, count))
    except Exception as e:
        return f"CHYBA: {e}"
    return f"OK, nahrazeno {count}x v {path}."


def tool_list_dir(args):
    path = args.get("path") or "."
    try:
        items = sorted(os.listdir(path))
        return "\n".join(items)[:4000] or "(prazdna slozka)"
    except Exception as e:
        return f"CHYBA: {e}"


def tool_web_fetch(args):
    url = args.get("url", "")
    if not url.startswith("http"):
        url = "https://" + url
    print(f"{C['dim']}  stahuji {url}{C['reset']}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw, enc = r.read(), r.headers.get("Content-Encoding", "")
        if enc == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        elif enc == "deflate":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        h = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"CHYBA: {e}"
    # titulky z odkazu
    seen, titles = set(), []
    for l in re.findall(r"<a[^>]*>(.*?)</a>", h, re.S):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", l))).strip()
        if 25 < len(t) < 140 and t not in seen:
            seen.add(t); titles.append(t)
    # ocisteny text jako fallback
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)
    body = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
    out = ""
    if titles:
        out += "TITULKY/ODKAZY:\n" + "\n".join("- " + t for t in titles[:40]) + "\n\n"
    out += "TEXT:\n" + body[:4000]
    return out[:6000] if out.strip() else "(stranka bez ctitelneho obsahu - asi JavaScript)"


DISPATCH = {"run_bash": tool_run_bash, "read_file": tool_read_file,
            "write_file": tool_write_file, "edit_file": tool_edit_file,
            "list_dir": tool_list_dir, "web_fetch": tool_web_fetch}


def chat(messages):
    """Streamuje odpoved z ollama; tokeny vypisuje zive a vrati cely message."""
    payload = {"model": MODEL, "messages": messages, "tools": TOOLS,
               "think": False, "stream": True}
    req = urllib.request.Request(OLLAMA, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    parts, tool_calls, prefixed = [], [], False
    sys.stdout.write(f"{C['dim']}  (generuji...){C['reset']}")
    sys.stdout.flush()
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            raw = raw.strip()
            if not raw:
                continue
            chunk = json.loads(raw)
            m = chunk.get("message", {})
            delta = m.get("content")
            if delta:
                if not prefixed:
                    sys.stdout.write(f"\r{' '*16}\r{C['cyan']}agent>{C['reset']} ")
                    prefixed = True
                sys.stdout.write(delta)
                sys.stdout.flush()
                parts.append(delta)
            if m.get("tool_calls"):
                tool_calls.extend(m["tool_calls"])
            if chunk.get("done"):
                break
    sys.stdout.write("\r" + " " * 16 + "\r")  # smaz "(generuji...)" kdyz nebyl text
    if prefixed:
        print()
    msg = {"role": "assistant", "content": "".join(parts)}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def main():
    print(f"{C['bold']}{C['cyan']}Lokalni agent ({MODEL}){C['reset']} "
          f"{'[YOLO]' if YOLO else '[potvrzovani]'} - 'exit' pro konec.\n")
    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            user = input(f"{C['bold']}{C['green']}ty> {C['reset']}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nNashledanou.")
            break
        if user.lower() in ("exit", "quit", "konec", "/bye"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})

        for _ in range(MAX_STEPS):
            try:
                msg = chat(messages)
            except Exception as e:
                print(f"{C['red']}Chyba spojeni s ollama: {e}{C['reset']}")
                break
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                break
            for call in calls:
                fn = call["function"]["name"]
                args = call["function"].get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                print(f"{C['yellow']}  -> {fn}({json.dumps(args, ensure_ascii=False)}){C['reset']}")
                result = DISPATCH.get(fn, lambda a: f"neznamy nastroj {fn}")(args)
                messages.append({"role": "tool", "tool_name": fn, "content": str(result)})
        print()


if __name__ == "__main__":
    main()
