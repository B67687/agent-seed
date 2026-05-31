#!/usr/bin/env python3
import subprocess, time
from datetime import datetime, timezone
from pathlib import Path
from openai import OpenAI

ROOT = Path(__file__).parent
LOG = ROOT / ".daemon.log"
client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="not-needed")


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG, "a") as f:
        f.write(f"[{ts}] {msg}\n")
    print(f"[{ts}] {msg}", flush=True)


def sh(cmd, t=120):
    r = subprocess.run(
        ["bash", "-c", cmd], capture_output=True, text=True, timeout=t, cwd=ROOT
    )
    return (r.stdout + r.stderr)[:500]


log("start")
while True:
    log("=" * 40)
    s = sh("git status --short")[:500]
    r = sh("git log --oneline -3")[:300]
    g = (ROOT / "GOAL.md").read_text().strip()
    c = (ROOT / "CHANGELOG.md").read_text().strip()[-1500:]
    f = sh("find . -maxdepth 2 -not -path ./.git -type f | head -15")[:300]
    p = f"<|think_off|>GOAL: {g}\nState:\n{s}\nRecent:\n{r}\nFiles:\n{f}\nCHANGELOG:\n{c}\n\nOutput ONE shell command starting with $. Then update CHANGELOG.md. Commands only."
    log(f"LLM ({len(p)}c)...")
    t0 = time.time()
    resp = client.chat.completions.create(
        model="Qwen3.6-27B-Q4_K_M.gguf",
        messages=[{"role": "user", "content": p}],
        max_tokens=200,
        temperature=0.1,
    )
    txt = resp.choices[0].message.content or ""
    log(f"OK {time.time() - t0:.0f}s ({len(txt)}c): {txt[:200]}")
    for line in txt.split("\n"):
        l = line.strip()
        if l.startswith("$ "):
            log(f"> {l[2:]}")
            log(sh(l[2:]))
    # Use AI's first line as commit message
    msg = txt.strip().split("\n")[0][:80] if txt.strip() else "auto"
    log(sh("git add -A 2>/dev/null || true"))
    log(sh('git commit -m "' + msg + '" 2>/dev/null || true'))
    log(sh("git push origin main 2>/dev/null || echo push-failed"))
    time.sleep(60)
