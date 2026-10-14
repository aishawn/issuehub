"""Run a real Chromium smoke test against an isolated TeamAI instance."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]


def wait_until_healthy(process: subprocess.Popen, base_url: str, log_file) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            log_file.seek(0)
            raise RuntimeError(f"TeamAI exited before becoming healthy:\n{log_file.read().decode(errors='replace')}")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("TeamAI did not become healthy within 20 seconds")


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def run_smoke(screenshots_dir: Path | None = None) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="teamai-browser-smoke-") as data_dir:
        env = os.environ.copy()
        env["APP_DB"] = str(Path(data_dir) / "browser-smoke.db")
        env["APP_ENV"] = "development"
        env["SYNC_INTERVAL_SECONDS"] = "0"
        for name in ("TEAMAI_API_KEY", "GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID"):
            env.pop(name, None)

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        with tempfile.TemporaryFile() as server_log:
            server = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", str(port), "--log-level", "error",
                ],
                cwd=ROOT,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            try:
                wait_until_healthy(server, base_url, server_log)
                page_errors: list[str] = []
                screenshot_paths: dict[str, str] = {}

                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1440, "height": 1000})
                        page.on("pageerror", lambda error: page_errors.append(str(error)))
                        response = page.goto(base_url, wait_until="networkidle")
                        check(bool(response and response.status == 200), "home page returns HTTP 200")
                        check(page.title() == "TeamAI Issue Hub", "page title is present")
                        page.locator("#username-input").fill("browser-smoke-user")
                        page.locator("#user-login-form button[type='submit']").click()
                        page.locator("#user-login").wait_for(state="hidden")
                        check(page.locator(".nav-item").count() == 3, "main navigation only shows overview, issues, and notes")
                        page.locator("[data-page='issues']").click()

                        page.locator("#top-new-record").click()
                        page.locator("[data-kind='note']").click()
                        page.locator("#title").fill("Browser smoke: personal note")
                        page.locator("#content").fill("Personal note visibility check")
                        page.locator("#project").fill("browser-smoke")
                        page.locator("#work-type").select_option(label="工作日常")
                        page.locator("#note-visibility").select_option("private")
                        page.locator("#submit").click()
                        personal_note = page.locator("#notes-results .record").filter(has_text="Browser smoke: personal note")
                        personal_note.wait_for(timeout=5000)
                        page.locator("#user-switch").click()
                        page.locator("#username-input").fill("browser-smoke-other")
                        page.locator("#user-login-form button[type='submit']").click()
                        personal_note.wait_for(state="detached", timeout=5000)
                        page.locator("#user-switch").click()
                        page.locator("#username-input").fill("browser-smoke-user")
                        page.locator("#user-login-form button[type='submit']").click()
                        page.locator("#notes-results .record").filter(has_text="Browser smoke: personal note").wait_for(timeout=5000)
                        page.locator("[data-page='issues']").click()

                        page.locator("#top-new-record").click()
                        page.locator("[data-kind='issue']").click()
                        page.locator("#content").fill("Browser smoke: MCP configuration failed to load")
                        page.locator("#work-type").select_option(label="模型部署")
                        page.locator("#submit").click()
                        page.get_by_text("已保存，记录已进入团队库。", exact=True).wait_for(timeout=5000)

                        record = page.locator("#results .issue-row").filter(has_text="Browser smoke: MCP configuration failed to load")
                        record.wait_for()
                        record.click()
                        page.locator("#detail").wait_for(state="visible")
                        check(page.evaluate("document.activeElement.id") == "detail-title", "opening detail focuses its heading")

                        page.locator("#edit-record").click()
                        page.locator("#edit-title").fill("Browser smoke: editable issue")
                        page.locator("#edit-content").fill("Browser smoke: MCP configuration failed to load")
                        page.locator("#save-edit").click()
                        page.locator("#detail-title").get_by_text("Browser smoke: editable issue").wait_for(timeout=5000)
                        check(page.locator("#detail-content").inner_text() == "Browser smoke: MCP configuration failed to load", "record edits persist through the management UI")

                        page.locator("#solution-input").fill("补充环境变量并重启服务")
                        page.locator("#verification-input").fill("重复启动服务三次，每次首次 MCP 请求均成功")
                        page.locator("#resolve-issue").click()
                        page.locator("#reopen-issue").wait_for(state="visible", timeout=5000)
                        page.locator("#solution-input").fill("回归验证发现问题仍存在")
                        page.locator("#reopen-issue").click()
                        page.locator("#detail-meta").get_by_text("待处理", exact=True).wait_for(timeout=5000)
                        page.keyboard.press("Escape")
                        page.locator("#detail").wait_for(state="hidden")
                        check(page.evaluate("document.activeElement.classList.contains('issue-row')"), "closing updated detail returns focus to the record")

                        source_issue = page.locator("#results .issue-row").filter(has_text="Browser smoke: editable issue").first
                        source_issue.click()
                        page.locator("#detail").wait_for(state="visible")
                        page.keyboard.press("Escape")
                        page.locator("#detail").wait_for(state="hidden")

                        # Hold a list refresh in flight, close the detail panel,
                        # then ensure replacing the focused record preserves focus.
                        page.locator("#results .issue-row").first.click()
                        page.locator("#detail").wait_for(state="visible")
                        page.evaluate("""() => {
                          const originalFetch = window.fetch.bind(window);
                          window.__searchHeld = false;
                          window.fetch = (input, ...args) => originalFetch(input, ...args).then(response => {
                            if (String(input).startsWith('/v1/search?')) {
                              window.__searchHeld = true;
                              console.log('__teamai_search_held__');
                              return new Promise(resolve => { window.__releaseSearch = () => resolve(response); });
                            }
                            return response;
                          });
                        }""")
                        page.locator("#results .issue-row").first.focus()
                        with page.expect_console_message(lambda message: message.text == "__teamai_search_held__", timeout=5000):
                            page.evaluate("() => { loadRecords(); }")
                        page.keyboard.press("Escape")
                        page.locator("#detail").wait_for(state="hidden")
                        page.evaluate("window.__releaseSearch()")
                        page.locator('#results[aria-busy="false"]').wait_for(timeout=5000)
                        page.locator("#results .issue-row:focus").wait_for(timeout=5000)
                        check(True, "list refresh preserves focus after closing detail")
                        check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "desktop viewport has no horizontal overflow")

                        if screenshots_dir:
                            screenshots_dir.mkdir(parents=True, exist_ok=True)
                            desktop = screenshots_dir / "desktop.png"
                            page.screenshot(path=desktop, full_page=True)
                            screenshot_paths["desktop"] = str(desktop)

                        page.set_viewport_size({"width": 390, "height": 844})
                        page.reload(wait_until="networkidle")
                        check(page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "390px mobile viewport has no horizontal overflow")
                        check(
                            page.locator(".metric").first.evaluate("element => element.getBoundingClientRect().height") >= 44,
                            "mobile metric touch target is at least 44px high",
                        )
                        if screenshots_dir:
                            mobile = screenshots_dir / "mobile.png"
                            page.screenshot(path=mobile, full_page=True)
                            screenshot_paths["mobile"] = str(mobile)
                    finally:
                        browser.close()

                check(not page_errors, "browser has no uncaught JavaScript errors")
                return {"status": "passed", "chromium": browser.version, "screenshots": screenshot_paths}
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run TeamAI browser smoke tests using an isolated SQLite database")
    parser.add_argument("--screenshots-dir", type=Path, help="Optional directory for desktop and mobile screenshots")
    args = parser.parse_args()
    try:
        result = run_smoke(args.screenshots_dir)
    except Exception as exc:
        print(f"Browser smoke failed: {exc}", file=sys.stderr)
        if "Executable doesn't exist" in str(exc):
            print("Install the matching browser with: python -m playwright install chromium", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
