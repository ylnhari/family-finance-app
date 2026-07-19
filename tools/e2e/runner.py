"""Browser E2E scenario runner (dev tool — not part of the stdlib test suite).

Drives a real Chrome over the DevTools protocol and executes UI scenarios
defined as data in a JSON file. Requires:
  - Chrome started with --remote-debugging-port (default 9333)
  - pip install websockets

Usage:
  python tools/e2e/runner.py --base http://127.0.0.1:9000 \
      --scenarios tools/e2e/scenarios.json [--filter tag] [--shots shots/]

Scenario format: [{"name": str, "tags": [str], "steps": [step, ...]}, ...]
Steps (one key each):
  {"goto": "/path"}                     navigate (base-relative) and wait for load
  {"click": "css selector"}             el.click() on first match (must exist)
  {"click_text": ["scope css", "text"]} click first element in scope containing text
  {"set": ["css", "value"]}             set input/select value + input/change events
  {"check": ["css", true]}              set checkbox state + change event
  {"eval": "js"}                        run js (await-ed; errors fail the scenario)
  {"wait": 800}                         sleep ms
  {"wait_for": "js expr"}               poll until truthy (5s timeout)
  {"assert": "js expr"}                 fail scenario if not truthy
  {"assert_text": ["css", "substr"]}    fail if selector's textContent lacks substr
  {"assert_count_lte": ["css", 25]}     fail if more than N elements match
  {"shot": "name.png"}                  save a screenshot
Console errors and uncaught exceptions are collected per scenario; a scenario
fails if any occur unless it carries the tag "allow-console-errors".
"""
import argparse
import asyncio
import base64
import itertools
import json
import sys
import urllib.request
from pathlib import Path

try:
    import websockets
except ImportError:
    sys.exit("pip install websockets (dev tool dependency)")

_id = itertools.count(1)


def new_tab(cdp):
    req = urllib.request.Request(f"{cdp}/json/new?about:blank", method="PUT")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


async def send(ws, method, params=None, timeout=30):
    mid = next(_id)
    await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg.get("id") == mid:
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result", {})


class Tab:
    def __init__(self, ws):
        self.ws = ws
        self.console_errors = []

    async def drain(self, wait=0.25):
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(self.ws.recv(), wait))
                m = msg.get("method")
                if m == "Log.entryAdded" and msg["params"]["entry"]["level"] == "error":
                    self.console_errors.append(msg["params"]["entry"].get("text", "")[:200])
                elif m == "Runtime.exceptionThrown":
                    d = msg["params"]["exceptionDetails"]
                    self.console_errors.append(
                        (d.get("exception", {}).get("description") or d.get("text", ""))[:200])
        except asyncio.TimeoutError:
            pass

    async def eval(self, expr):
        r = await send(self.ws, "Runtime.evaluate",
                       {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = r.get("result", {})
        if res.get("subtype") == "error":
            raise RuntimeError(f"eval failed: {res.get('description', '')[:200]}")
        if r.get("exceptionDetails"):
            raise RuntimeError(f"eval threw: {str(r['exceptionDetails'])[:200]}")
        return res.get("value")

    async def goto(self, url):
        await send(self.ws, "Page.navigate", {"url": url})
        for _ in range(80):
            state = await self.eval("document.readyState")
            if state == "complete":
                break
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.9)  # SPA render/fetch settle


def js_str(s):
    return json.dumps(s)


async def run_step(tab, base, step, shots_dir):
    if "goto" in step:
        await tab.goto(base + step["goto"])
    elif "click" in step:
        sel = step["click"]
        ok = await tab.eval(
            f"(() => {{ const e = document.querySelector({js_str(sel)}); if (!e) return false; e.click(); return true; }})()")
        if not ok:
            raise RuntimeError(f"no element for click: {sel}")
        await asyncio.sleep(0.5)
    elif "click_text" in step:
        scope, text = step["click_text"]
        ok = await tab.eval(
            f"(() => {{ const els = [...document.querySelectorAll({js_str(scope)})];"
            f" const e = els.find(x => x.textContent.includes({js_str(text)}));"
            f" if (!e) return false; e.click(); return true; }})()")
        if not ok:
            raise RuntimeError(f"no element in {scope} with text: {text}")
        await asyncio.sleep(0.5)
    elif "set" in step:
        sel, value = step["set"]
        ok = await tab.eval(
            f"(() => {{ const e = document.querySelector({js_str(sel)}); if (!e) return false;"
            f" e.value = {js_str(str(value))};"
            f" e.dispatchEvent(new Event('input', {{bubbles: true}}));"
            f" e.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()")
        if not ok:
            raise RuntimeError(f"no element for set: {sel}")
    elif "check" in step:
        sel, state = step["check"]
        ok = await tab.eval(
            f"(() => {{ const e = document.querySelector({js_str(sel)}); if (!e) return false;"
            f" e.checked = {'true' if state else 'false'};"
            f" e.dispatchEvent(new Event('change', {{bubbles: true}})); return true; }})()")
        if not ok:
            raise RuntimeError(f"no element for check: {sel}")
    elif "eval" in step:
        await tab.eval(step["eval"])
    elif "wait" in step:
        await asyncio.sleep(step["wait"] / 1000)
    elif "wait_for" in step:
        for _ in range(50):
            if await tab.eval(f"!!({step['wait_for']})"):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"wait_for timed out: {step['wait_for']}")
    elif "assert" in step:
        if not await tab.eval(f"!!({step['assert']})"):
            raise RuntimeError(f"assert failed: {step['assert']}")
    elif "assert_text" in step:
        sel, sub = step["assert_text"]
        ok = await tab.eval(
            f"(() => {{ const e = document.querySelector({js_str(sel)});"
            f" return !!e && e.textContent.includes({js_str(sub)}); }})()")
        if not ok:
            raise RuntimeError(f"assert_text failed: {sel} !~ {sub!r}")
    elif "assert_count_lte" in step:
        sel, n = step["assert_count_lte"]
        count = await tab.eval(f"document.querySelectorAll({js_str(sel)}).length")
        if count > n:
            raise RuntimeError(f"assert_count_lte failed: {sel} has {count} > {n}")
    elif "shot" in step:
        shot = await send(tab.ws, "Page.captureScreenshot", {"format": "png"}, timeout=40)
        path = Path(shots_dir) / step["shot"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(shot["data"]))
    else:
        raise RuntimeError(f"unknown step: {step}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--scenarios", required=True, nargs="+")
    ap.add_argument("--cdp", default="http://localhost:9333")
    ap.add_argument("--shots", default="tools/e2e/shots")
    ap.add_argument("--filter", default=None, help="only run scenarios carrying this tag")
    args = ap.parse_args()

    scenarios = []
    for f in args.scenarios:
        scenarios += json.loads(Path(f).read_text(encoding="utf-8"))
    if args.filter:
        scenarios = [s for s in scenarios if args.filter in s.get("tags", [])]

    tab_info = new_tab(args.cdp)
    passed, failed = 0, []
    async with websockets.connect(tab_info["webSocketDebuggerUrl"], max_size=80_000_000) as ws:
        tab = Tab(ws)
        await send(ws, "Page.enable")
        await send(ws, "Runtime.enable")
        await send(ws, "Log.enable")
        for sc in scenarios:
            tab.console_errors = []
            try:
                for step in sc["steps"]:
                    await run_step(tab, args.base, step, args.shots)
                    await tab.drain()
                if tab.console_errors and "allow-console-errors" not in sc.get("tags", []):
                    raise RuntimeError("console errors: " + "; ".join(tab.console_errors[:3]))
                passed += 1
                print(f"  PASS  {sc['name']}")
            except Exception as e:
                failed.append((sc["name"], str(e)[:250]))
                print(f"  FAIL  {sc['name']} — {str(e)[:250]}")
    print(f"\n{passed}/{len(scenarios)} passed, {len(failed)} failed")
    if failed:
        print("\nFailures:")
        for name, err in failed:
            print(f"  - {name}: {err}")
        sys.exit(1)


asyncio.run(main())
