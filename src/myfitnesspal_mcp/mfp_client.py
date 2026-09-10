import hashlib
import json
import uuid
from http.cookiejar import Cookie, CookieJar

import myfitnesspal
from curl_cffi import requests as cffi_requests
from myfitnesspal.exceptions import MyfitnesspalLoginError

from . import auth, config

RECONNECT_HINT = (
    "MyFitnessPal session expired or not connected. "
    "Re-run 'myfitnesspal-mcp auth' or update MFP_COOKIE, then retry."
)


class NotConnectedError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    """A request conclusively failed because authentication is invalid."""


class ClientInitializationError(RuntimeError):
    """A sanitized non-authentication client construction failure."""


class ProfileLookupError(MyfitnesspalLoginError):
    """Authentication succeeded but MFP's optional profile lookup failed."""


def is_auth_error(exc: Exception) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (AuthenticationError, NotConnectedError, MyfitnesspalLoginError)):
            return True
        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            status = getattr(current, "status_code", None)
        if status in (401, 403):
            return True
        current = current.__cause__ or current.__context__
    return False


class CurlCffiClient(myfitnesspal.Client):
    """myfitnesspal.Client over a curl_cffi browser-impersonating session.

    MyFitnessPal sits behind Cloudflare, which fingerprints the upstream
    cloudscraper transport as a bot and 403s even with valid cookies. A real
    Chrome TLS/JA3 fingerprint passes with just the NextAuth session cookie.
    """

    def __init__(
        self,
        cookiejar: CookieJar,
        username: str | None = None,
        impersonate: str | None = None,
    ):
        self._username_override = username
        self._client_instance_id = uuid.uuid4()
        self._request_counter = 0
        self._log_requests_to = None
        self.unit_aware = False
        self.session = cffi_requests.Session(
            impersonate=impersonate or config.impersonate(),
            timeout=config.request_timeout(),
        )
        self.session.cookies.update(cookiejar)
        try:
            self._auth_data = self._get_auth_data()
            self._user_metadata = self._get_user_metadata()
        except Exception:
            self.session.close()
            raise

    def _get_user_metadata(self):
        """MFP's v2 users endpoint 500s for some accounts; fall back to the
        configured username, which is all the diary URLs need."""
        try:
            meta = super()._get_user_metadata()
            if meta and meta.get("username"):
                return meta
        except MyfitnesspalLoginError:
            raise
        except Exception:
            pass
        username = self._username_override or auth.saved_username()
        if not username:
            raise ProfileLookupError(
                "Authenticated, but couldn't read your MyFitnessPal profile. "
                "Set MFP_USERNAME to your MyFitnessPal username (not email) and retry."
            )
        return {"username": username}


def cookies_to_jar(cookies: dict[str, str]) -> CookieJar:
    jar = CookieJar()
    for name, value in cookies.items():
        jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=".myfitnesspal.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
    return jar


def build_client(
    cookies: dict[str, str],
    username: str | None = None,
    impersonate: str | None = None,
) -> CurlCffiClient:
    return CurlCffiClient(
        cookies_to_jar(cookies), username=username, impersonate=impersonate
    )


_client: CurlCffiClient | None = None
_client_key: str | None = None


def _credentials_key(cookies: dict[str, str], username: str | None) -> str:
    encoded = json.dumps([username, sorted(cookies.items())], separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_client() -> CurlCffiClient:
    global _client, _client_key
    cookies = auth.load_cookies()
    if not cookies:
        raise NotConnectedError(RECONNECT_HINT)
    username = auth.saved_username()
    key = _credentials_key(cookies, username)
    if _client is not None and _client_key == key:
        return _client
    if _client is not None:
        reset()
    try:
        _client = build_client(cookies, username=username)
        _client_key = key
    except NotConnectedError:
        raise
    except Exception as exc:
        if is_auth_error(exc):
            raise AuthenticationError(RECONNECT_HINT) from exc
        raise ClientInitializationError("Could not initialize the MyFitnessPal client.") from exc
    return _client


def reset() -> None:
    global _client, _client_key
    old_client = _client
    _client = None
    _client_key = None
    session = getattr(old_client, "session", None)
    close = getattr(session, "close", None)
    if callable(close):
        close()
