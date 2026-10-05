"""Build the app by **running a file**, not by clicking a UI.

An app on the platform is defined by a DSL document (`app.dsl.yml`), and the platform's
console API can import it. So "搭好一个智能体应用" becomes:

    setup (first run) -> login -> import the DSL -> create an app API key -> print .env lines

Usage:

    uv run python agent/import_app.py --dsl agent/app.dsl.yml \
        --console-url http://localhost/console/api --email you@example.com

Two honest caveats (this script exists to be *tried*, not to be believed):

* I verified the endpoint names against the platform's source where I could
  (`POST /apps`, `POST /apps/imports`, `GET /apps/<id>/export`), but **have not run this
  against a live instance**. Where a path is inferred, the script says so and prints the raw
  response instead of pretending success.
* If the import endpoint rejects the payload shape, the guaranteed fallback is the platform's
  own "Import DSL" button -- still one action with a file, still no hand-building.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

DEFAULT_DSL = Path(__file__).resolve().parent / "app.dsl.yml"


class Step:
    """Prints what happened, including the raw body when it is not what we expected."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def ok(self, what: str, detail: str = "") -> None:
        print(f"  [OK]   {what}" + (f" -- {detail}" if detail else ""))

    def warn(self, what: str, detail: str = "") -> None:
        print(f"  [WARN] {what}" + (f" -- {detail}" if detail else ""))

    def fail(self, what: str, detail: str = "") -> None:
        self.failures.append(what)
        print(f"  [FAIL] {what}" + (f" -- {detail}" if detail else ""))


def _post(client: httpx.Client, url: str, payload: dict, headers: dict | None = None):
    try:
        return client.post(url, json=payload, headers=headers or {})
    except httpx.HTTPError as exc:
        print(f"  [FAIL] {url} -> {type(exc).__name__}: {exc}")
        return None


def login(client: httpx.Client, console: str, email: str, password: str, step: Step) -> str | None:
    """First-run setup, then login. Returns the console access token."""
    setup = _post(
        client, f"{console}/setup", {"email": email, "name": "voice", "password": password}
    )
    if setup is not None and setup.status_code < 400:
        step.ok("first-run setup", "admin account created")
    elif setup is not None:
        step.ok("setup not needed", f"HTTP {setup.status_code} (already set up)")


    resp = _post(client, f"{console}/login", {"email": email, "password": password})
    if resp is None or resp.status_code >= 400:
        body = resp.text[:200] if resp is not None else ""
        step.fail("login", f"HTTP {getattr(resp, 'status_code', '?')} {body}")
        return None
    token = (resp.json() or {}).get("access_token") or (resp.json() or {}).get("data", {}).get(
        "access_token"
    )
    if not token:
        step.fail("login", f"no access_token in response: {resp.text[:200]}")
        return None
    step.ok("login", "console token acquired")
    return token


def import_dsl(
    client: httpx.Client, console: str, token: str, dsl_text: str, step: Step
) -> str | None:
    """Import the app definition. Returns the new app id."""
    headers = {"Authorization": f"Bearer {token}"}
    # The payload field name for the YAML body is the one inferred bit here; try the two
    # spellings the source hints at and report the raw response if both are rejected.
    for field in ("yaml_content", "yaml_content_str"):
        resp = _post(
            client,
            f"{console}/apps/imports",
            {"mode": "yaml-content", field: dsl_text},
            headers,
        )
        if resp is None:
            continue
        if resp.status_code < 400:
            data = resp.json() or {}
            app_id = data.get("app_id") or data.get("id") or (data.get("app") or {}).get("id")
            step.ok("import DSL", f"app_id={app_id} (field={field})")
            return app_id
        step.warn(f"import with field '{field}'", f"HTTP {resp.status_code}: {resp.text[:200]}")

    step.fail("import DSL", "all payload shapes rejected -- use the platform's Import DSL button")
    return None


def create_api_key(
    client: httpx.Client, console: str, token: str, app_id: str, step: Step
) -> str | None:
    """Create the app API key the voice module will use. Path inferred from the source layout."""
    resp = _post(
        client,
        f"{console}/apps/{app_id}/api-keys",
        {"name": "voice-module"},
        {"Authorization": f"Bearer {token}"},
    )
    if resp is None:
        return None
    if resp.status_code >= 400:
        step.fail(
            "create API key",
            f"HTTP {resp.status_code}: {resp.text[:200]} -- create it in the app's API page",
        )
        return None
    token_value = (resp.json() or {}).get("token") or (resp.json() or {}).get("api_key")
    if not token_value:
        step.fail("create API key", f"no token in response: {resp.text[:200]}")
        return None
    step.ok("create API key", f"{str(token_value)[:12]}...")
    return str(token_value)


def main() -> int:
    ap = argparse.ArgumentParser(description="import the agent definition file and mint an API key")
    ap.add_argument("--dsl", type=Path, default=DEFAULT_DSL)
    ap.add_argument("--console-url", default="http://localhost/console/api")
    ap.add_argument("--email", default="admin@example.com")
    ap.add_argument("--password", default="Passw0rd!123")
    args = ap.parse_args()

    if not args.dsl.exists():
        print(f"dsl file not found: {args.dsl}")
        return 2
    dsl_text = args.dsl.read_text(encoding="utf-8")

    print("=" * 72)
    print(f"building the app from a file: {args.dsl}")
    print("=" * 72)
    step = Step()
    with httpx.Client(timeout=60.0) as client:
        token = login(client, args.console_url.rstrip("/"), args.email, args.password, step)
        app_id = (
            import_dsl(client, args.console_url.rstrip("/"), token, dsl_text, step)
            if token
            else None
        )
        api_key = (
            create_api_key(client, args.console_url.rstrip("/"), token, app_id, step)
            if app_id and token
            else None
        )

    print("-" * 72)
    if api_key:
        print("put these in .env:")
        print(f"  BRAIN_BASE_URL={args.console_url.rstrip('/').replace('/console/api', '/v1')}")
        print(f"  BRAIN_API_KEY={api_key}")
    else:
        print("fall back to the platform's UI for the step(s) marked [FAIL]/[WARN] above --")
        print("it is still one action with this file, and nothing else here is affected.")
    return 1 if step.failures else 0


if __name__ == "__main__":
    sys.exit(main())
