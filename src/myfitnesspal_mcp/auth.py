import getpass
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import config

SESSION_COOKIE = "__Secure-next-auth.session-token"

INSTRUCTIONS = """\
Connect your MyFitnessPal account
---------------------------------
1. Log in at https://www.myfitnesspal.com in your browser.
2. Open DevTools (F12) -> Application (Chrome) or Storage (Firefox) -> Cookies.
3. Copy the value of the '__Secure-next-auth.session-token' cookie.
   (Pasting the entire Cookie header from any request also works.)
"""


class CredentialError(RuntimeError):
    """A safe-to-display error concerning locally stored credentials."""


def parse_cookie_input(text: str) -> dict[str, str]:
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:"):].strip()
    if not text:
        return {}
    if "=" not in text:
        return {SESSION_COOKIE: text}
    cookies = {}
    for part in text.split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            if name.strip() and value.strip():
                cookies[name.strip()] = value.strip()
    return cookies


def _validate_saved(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CredentialError("saved credentials have an invalid format; run 'myfitnesspal-mcp auth'")
    cookies = value.get("cookies")
    if cookies is not None and (
        not isinstance(cookies, dict)
        or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in cookies.items())
    ):
        raise CredentialError("saved credentials have invalid cookies; run 'myfitnesspal-mcp auth'")
    username = value.get("username")
    if username is not None and (not isinstance(username, str) or not username.strip()):
        raise CredentialError("saved credentials have an invalid username; run 'myfitnesspal-mcp auth'")
    return value


def _read_saved() -> dict[str, Any]:
    path = config.cookies_path()
    if not path.exists():
        return {}
    try:
        return _validate_saved(json.loads(path.read_text(encoding="utf-8")))
    except CredentialError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CredentialError("could not read saved credentials; run 'myfitnesspal-mcp auth'") from exc


def load_cookies() -> dict[str, str] | None:
    env = config.cookie_env()
    if env:
        cookies = parse_cookie_input(env)
        if not cookies:
            raise CredentialError("MFP_COOKIE does not contain a cookie")
        return cookies
    saved = _read_saved().get("cookies")
    if saved:
        return saved
    return None


def saved_username() -> str | None:
    if config.username_env():
        return config.username_env()
    return _read_saved().get("username")


def _restrict_windows(path: Path) -> None:
    identity = subprocess.run(
        ["whoami"], capture_output=True, text=True, check=False
    )
    principal = identity.stdout.strip()
    if identity.returncode or not principal:
        raise CredentialError("could not secure saved credentials")
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:F"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    if result.returncode:
        raise CredentialError("could not secure saved credentials")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temporary)
    try:
        os.chmod(temp_path, 0o600)
        if os.name == "nt":
            _restrict_windows(temp_path)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def save_cookies(cookies: dict[str, str], username: str | None = None) -> None:
    if not cookies or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in cookies.items()):
        raise CredentialError("refusing to save invalid cookies")
    saved = _read_saved()
    saved["cookies"] = dict(cookies)
    if username:
        saved["username"] = username.strip()
    _validate_saved(saved)
    _atomic_write(config.cookies_path(), json.dumps(saved, indent=2) + "\n")


def read_cookie_paste() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Paste cookie (input hidden): ")
    print("(no terminal detected — reading the cookie from stdin)", file=sys.stderr)
    return sys.stdin.readline()


def _validate(cookies: dict[str, str]):
    """Builds a client from the cookies; when only the profile lookup fails (a
    known MFP issue for accounts that log in by email), asks for the username
    and retries instead of failing the whole flow."""
    from . import mfp_client

    try:
        return mfp_client.build_client(cookies)
    except mfp_client.ProfileLookupError:
        if not sys.stdin.isatty():
            raise
        print(
            "\nYour cookie works, but MyFitnessPal couldn't return your profile "
            "(a known issue for some accounts)."
        )
        username = input("Your MyFitnessPal username (not email): ").strip()
        if not username:
            raise
        return mfp_client.build_client(cookies, username=username)


def run_auth_flow() -> int:
    from . import mfp_client, refresh

    print(INSTRUCTIONS, flush=True)
    try:
        pasted = read_cookie_paste()
    except EOFError:
        pasted = ""
    if not pasted.strip():
        print(
            "No cookie received. Run this in an interactive terminal, or pipe "
            "the token in: myfitnesspal-mcp auth < token.txt",
            file=sys.stderr,
        )
        return 1

    cookies = parse_cookie_input(pasted)
    if SESSION_COOKIE not in cookies:
        print("The input did not contain a MyFitnessPal session cookie.", file=sys.stderr)
        return 1
    print("Validating with MyFitnessPal...")
    try:
        client = _validate(cookies)
    except Exception:
        print("Those cookies did not authenticate. Check the cookie and try again.", file=sys.stderr)
        return 1

    save_cookies(cookies, username=client.effective_username)
    mfp_client.reset()
    print(f"Connected as {client.effective_username}. Credentials saved securely.")

    if refresh.available():
        print("Seeding the browser profile for automatic session refresh...")
        try:
            refresh.seed_profile(cookies, username=client.effective_username)
            print("Auto-refresh ready.")
        except Exception:
            print("Could not seed automatic refresh; manual re-authentication remains available.", file=sys.stderr)
            print("The server still works; sessions just need a manual re-auth when they expire.")
    else:
        print(
            "Optional: install with the [autorefresh] extra and run auth again to "
            "enable automatic session refresh via a headless browser."
        )
    return 0
