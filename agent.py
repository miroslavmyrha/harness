#!/usr/bin/env python3
"""Minimal local agent harness for ollama models with tool calling.
Usage:    python3 agent.py          (asks before bash/file writes)
          python3 agent.py --yolo   (runs everything without asking)
Env:      AGENT_MODEL  - model name (default asistent-agent)
          AGENT_OLLAMA - ollama base URL (default http://localhost:11434)
          AGENT_ROOT   - directory the agent may write into (default $HOME)
"""
import json, os, sys, subprocess, urllib.request, gzip, zlib, re, html, fnmatch

OLLAMA = os.environ.get("AGENT_OLLAMA", "http://localhost:11434") + "/api/chat"
MODEL = os.environ.get("AGENT_MODEL", "asistent-agent")
ROOT = os.path.realpath(os.environ.get("AGENT_ROOT", os.path.expanduser("~")))
YOLO = "--yolo" in sys.argv
MAX_STEPS = 25  # cap on tool calls per user turn
# commands that require confirmation even in YOLO mode
DANGEROUS = re.compile(r"\bsudo\b|\brm\s+-\w*[rf]|\bmkfs|\bdd\b|>\s*/dev/"
                       r"|\bshutdown\b|\breboot\b|\bchmod\s+-R|\bchown\s+-R")

# colors
C = dict(reset="\033[0m", dim="\033[2m", cyan="\033[36m",
         yellow="\033[33m", green="\033[32m", red="\033[31m", bold="\033[1m")

SYSTEM = ("You are a concise agent on a local computer. You have the tools "
          "run_bash, read_file, write_file, edit_file, grep, list_dir, web_fetch. "
          "Call a tool when needed; otherwise answer in the user's language. "
          "Work in small steps. Modify existing files with edit_file (exact "
          "string replacement); use write_file only for new files or a full "
          "rewrite.")

TOOLS = [
    {"type": "function", "function": {"name": "run_bash",
        "description": "Run a shell command and return its output.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "command to run"}},
            "required": ["command"]}}},
    {"type": "function", "function": {"name": "read_file",
        "description": "Read file contents (max ~6000 chars at a time; for "
                       "longer files continue with the from_line parameter).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "from_line": {"type": "integer", "description": "start reading at this line (default 1)"}},
            "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file",
        "description": "Write (overwrite) the full contents of a file. To "
                       "modify an existing file prefer edit_file.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "edit_file",
        "description": "Edit a file by exact string replacement. old_string "
                       "must exist in the file exactly (including indentation "
                       "and newlines) and be unique; otherwise add more "
                       "surrounding context.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "exact text to replace"},
            "new_string": {"type": "string", "description": "new text"},
            "replace_all": {"type": "boolean", "description": "replace every occurrence (default false)"}},
            "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {"name": "grep",
        "description": "Search files for a regex, recursively. Returns "
                       "file:line: text matches. Skips hidden dirs, "
                       "node_modules and binary files.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "regular expression"},
            "path": {"type": "string", "description": "file or directory to search (default '.')"},
            "glob": {"type": "string", "description": "filename filter, e.g. '*.py' (default all files)"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "list_dir",
        "description": "List directory contents.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "default '.'"}},
            "required": []}}},
    {"type": "function", "function": {"name": "web_fetch",
        "description": "Fetch a web page and return link titles + cleaned "
                       "text. Note: does not work on JavaScript-rendered pages.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "page address"}},
            "required": ["url"]}}},
]


def confirm(action, force=False):
    if YOLO and not force:
        return True
    try:
        ans = input(f"{C['yellow']}  allow {action}? [y/N] {C['reset']}").strip().lower()
    except EOFError:
        return False
    return ans in ("y", "yes", "a", "ano")


def safe_path(path):
    """Return the real path if it lies inside ROOT, else None (no writes outside ROOT)."""
    rp = os.path.realpath(path)
    return rp if rp == ROOT or rp.startswith(ROOT + os.sep) else None


def tool_run_bash(args):
    cmd = args.get("command", "")
    print(f"{C['dim']}  $ {cmd}{C['reset']}")
    if not confirm(f"command: {cmd}", force=bool(DANGEROUS.search(cmd))):
        return "DENIED by user."
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:4000] if out else f"(no output, exit code {r.returncode})"
    except subprocess.TimeoutExpired:
        return "ERROR: command exceeded the 120s timeout."
    except Exception as e:
        return f"ERROR: {e}"


def tool_read_file(args):
    try:
        start = max(int(args.get("from_line") or 1), 1)
    except (TypeError, ValueError):
        start = 1
    try:
        if os.path.getsize(args["path"]) > 5_000_000:
            return "ERROR: file is larger than 5 MB, read it in pieces via run_bash."
        with open(args["path"], "r", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return f"ERROR: {e}"
    if start > len(lines):
        return f"ERROR: the file only has {len(lines)} lines."
    out, size = [], 0
    for i, line in enumerate(lines[start - 1:], start):
        if size + len(line) > 6000 and out:
            out.append(f"\n[truncated - the file has {len(lines)} lines, "
                       f"continue with from_line={i}]")
            break
        out.append(line)
        size += len(line)
    return "".join(out) or "(empty file)"


def tool_write_file(args):
    path, content = args.get("path", ""), args.get("content", "")
    if not safe_path(path):
        return f"ERROR: writing outside {ROOT} is forbidden."
    print(f"{C['dim']}  write -> {path} ({len(content)} chars){C['reset']}")
    if not confirm(f"write to {path}"):
        return "DENIED by user."
    try:
        with open(path, "w") as f:
            f.write(content)
        return f"OK, wrote {len(content)} chars to {path}."
    except Exception as e:
        return f"ERROR: {e}"


def tool_edit_file(args):
    path = args.get("path", "")
    old, new = args.get("old_string", ""), args.get("new_string", "")
    replace_all = bool(args.get("replace_all", False))
    if not safe_path(path):
        return f"ERROR: writing outside {ROOT} is forbidden."
    if not old:
        return "ERROR: old_string must not be empty."
    if old == new:
        return "ERROR: old_string and new_string are identical."
    try:
        with open(path, "r", errors="replace") as f:
            text = f.read()
    except Exception as e:
        return f"ERROR: {e}"
    n = text.count(old)
    if n == 0:
        return ("ERROR: old_string not found in the file - it must match "
                "exactly, including indentation and newlines.")
    if n > 1 and not replace_all:
        return (f"ERROR: old_string found {n}x, it must be unique - add more "
                "surrounding context, or use replace_all=true.")
    count = n if replace_all else 1
    print(f"{C['dim']}  edit -> {path} ({count}x replacement, "
          f"-{len(old)}/+{len(new)} chars){C['reset']}")
    if not confirm(f"edit of {path}"):
        return "DENIED by user."
    try:
        with open(path, "w") as f:
            f.write(text.replace(old, new, count))
    except Exception as e:
        return f"ERROR: {e}"
    return f"OK, replaced {count}x in {path}."


def tool_grep(args):
    pattern, path = args.get("pattern", ""), args.get("path") or "."
    glob = args.get("glob") or "*"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: invalid regex: {e}"

    def search_one(fp, hits):
        try:
            if os.path.getsize(fp) > 5_000_000:
                return
            with open(fp, "r", errors="replace") as f:
                for i, line in enumerate(f, 1):
                    if "\0" in line:  # binary file
                        return
                    if rx.search(line):
                        hits.append(f"{fp}:{i}: {line.rstrip()[:200]}")
                        if len(hits) >= 100:
                            return
        except OSError:
            pass

    hits = []
    if os.path.isfile(path):
        search_one(path, hits)
    else:
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")
                       and d not in ("node_modules", "__pycache__", "vendor")]
            for name in names:
                if fnmatch.fnmatch(name, glob):
                    search_one(os.path.join(root, name), hits)
                if len(hits) >= 100:
                    break
            if len(hits) >= 100:
                hits.append("[truncated at 100 matches - narrow the pattern or glob]")
                break
    out = "\n".join(hits)
    if len(out) > 6000:
        out = out[:6000].rsplit("\n", 1)[0] + "\n[output truncated - narrow the pattern or glob]"
    return out or "(no matches)"


def tool_list_dir(args):
    path = args.get("path") or "."
    try:
        items = sorted(os.listdir(path))
        return "\n".join(items)[:4000] or "(empty directory)"
    except Exception as e:
        return f"ERROR: {e}"


def tool_web_fetch(args):
    url = args.get("url", "")
    if not url.startswith("http"):
        url = "https://" + url
    print(f"{C['dim']}  fetching {url}{C['reset']}")
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
        return f"ERROR: {e}"
    # link titles
    seen, titles = set(), []
    for l in re.findall(r"<a[^>]*>(.*?)</a>", h, re.S):
        t = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", l))).strip()
        if 25 < len(t) < 140 and t not in seen:
            seen.add(t); titles.append(t)
    # cleaned text as fallback
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S)
    body = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()
    out = ""
    if titles:
        out += "TITLES/LINKS:\n" + "\n".join("- " + t for t in titles[:40]) + "\n\n"
    out += "TEXT:\n" + body[:4000]
    return out[:6000] if out.strip() else "(page has no readable content - probably JavaScript)"


DISPATCH = {"run_bash": tool_run_bash, "read_file": tool_read_file,
            "write_file": tool_write_file, "edit_file": tool_edit_file,
            "grep": tool_grep, "list_dir": tool_list_dir,
            "web_fetch": tool_web_fetch}


def chat(messages):
    """Streams a response from ollama; prints tokens live and returns the full message."""
    payload = {"model": MODEL, "messages": messages, "tools": TOOLS,
               "think": False, "stream": True}
    req = urllib.request.Request(OLLAMA, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    parts, tool_calls, prefixed = [], [], False
    sys.stdout.write(f"{C['dim']}  (generating...){C['reset']}")
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
    sys.stdout.write("\r" + " " * 16 + "\r")  # erase "(generating...)" if there was no text
    if prefixed:
        print()
    msg = {"role": "assistant", "content": "".join(parts)}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def main():
    print(f"{C['bold']}{C['cyan']}Local agent ({MODEL}){C['reset']} "
          f"{'[YOLO]' if YOLO else '[confirming]'} - 'exit' to quit.\n")
    messages = [{"role": "system", "content": SYSTEM}]
    while True:
        try:
            user = input(f"{C['bold']}{C['green']}you> {C['reset']}").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if user.lower() in ("exit", "quit", "konec", "/bye"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})

        for _ in range(MAX_STEPS):
            try:
                msg = chat(messages)
            except KeyboardInterrupt:
                print(f"\n{C['red']}Interrupted.{C['reset']}")
                messages.append({"role": "assistant",
                                 "content": "(interrupted by user)"})
                break
            except Exception as e:
                print(f"{C['red']}Error talking to ollama: {e}{C['reset']}")
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
                result = DISPATCH.get(fn, lambda a: f"unknown tool {fn}")(args)
                messages.append({"role": "tool", "tool_name": fn, "content": str(result)})
        print()


if __name__ == "__main__":
    main()
