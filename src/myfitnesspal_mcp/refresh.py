from pathlib import Path
from threading import Lock

from . import auth, config, mfp_client

MFP_URL = "https://www.myfitnesspal.com/"
SETTLE_MS = 4000
_REFRESH_LOCK = Lock()


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def profile_dir(username: str | None = None, *, create: bool = False) -> Path:
    username = username or auth.saved_username()
    if not username:
        raise auth.CredentialError("a username is required for automatic refresh")
    path = config.account_data_dir(username) / "browser-profile"
    return config.owned_directory(path) if create else path


def profile_seeded(username: str | None = None) -> bool:
    try:
        return (profile_dir(username) / ".seeded").is_file()
    except auth.CredentialError:
        return False


def _visit_and_harvest(
    seed_cookies: dict[str, str] | None, username: str | None = None
) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(profile_dir(username, create=True)), headless=True
        )
        try:
            if seed_cookies:
                context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": value,
                            "domain": ".myfitnesspal.com",
                            "path": "/",
                            "secure": True,
                        }
                        for name, value in seed_cookies.items()
                    ]
                )
            page = context.pages[0]
            page.goto(MFP_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(SETTLE_MS)
            harvested = {}
            for cookie in context.cookies(MFP_URL):
                harvested[cookie["name"]] = cookie["value"]
            return harvested
        finally:
            context.close()


def seed_profile(cookies: dict[str, str], username: str | None = None) -> None:
    with _REFRESH_LOCK:
        harvested = _visit_and_harvest(cookies, username)
        if auth.SESSION_COOKIE not in harvested:
            raise mfp_client.AuthenticationError(
                "the browser visit did not produce a MyFitnessPal session cookie"
            )
        auth.save_cookies(harvested, username=username)
        (profile_dir(username, create=True) / ".seeded").touch(mode=0o600)
        mfp_client.reset()


def refresh_session() -> None:
    """Rotate the session by revisiting MFP in the seeded headless browser
    profile, then persist the fresh cookies and drop the cached client."""
    with _REFRESH_LOCK:
        if available() and profile_seeded():
            harvested = _visit_and_harvest(None)
            if auth.SESSION_COOKIE not in harvested:
                raise mfp_client.AuthenticationError(
                    "automatic refresh did not produce a session cookie"
                )
            auth.save_cookies(harvested)
        mfp_client.reset()
