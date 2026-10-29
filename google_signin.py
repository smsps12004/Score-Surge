"""Continue with Google (29 Oct 2026).

Supabase does the Google part. This file only does the bookkeeping that Streamlit
makes awkward.

Why the bookkeeping: signing in with Google sends the browser away (to Google, then
Supabase) and back. Supabase hands back a one-time `code` in the address bar, and
the app trades it for a login. To stop a stolen code being used by somebody else,
the trade also needs a secret, the "code verifier", that only the tab which started
the sign-in knows (this is PKCE, the standard way to do it).

The Supabase library keeps that secret in the client's memory. That works in a web
page, but not in Streamlit: the browser coming back from Google opens a brand new
Streamlit session with a brand new client, and the secret is gone. So the secret is
kept here instead, on the server, filed under a random `google_flow` ID that rides
along in the return address. The ID alone is useless: it only finds the secret,
and Google's code is still needed too. Each one works once and expires after 10
minutes.

One server process holds the list in memory (Render runs one). If the app restarts
while a sailor is on Google's page, their sign-in fails with a plain "try again"
message. Nothing is lost.

Nothing in here talks to the network, so run_checks.py tests it directly.
"""

import base64
import hashlib
import secrets
import threading
import time
import urllib.parse

FLOW_PARAM = "google_flow"      # query parameter carrying the flow ID back to the app
FLOW_TTL_SECONDS = 600          # a sign-in must come back within 10 minutes
MAX_PENDING = 5000              # hard cap so a flood of taps can't grow memory forever


def make_verifier() -> str:
    """A fresh PKCE code verifier: 64 random URL-safe characters (spec allows 43-128)."""
    return secrets.token_urlsafe(48)[:64]


def challenge_for(verifier: str) -> str:
    """The S256 challenge Google/Supabase is shown in place of the verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class PendingFlows:
    """Sign-ins that have left for Google and not come back yet. Thread-safe."""

    def __init__(self, ttl: int = FLOW_TTL_SECONDS, cap: int = MAX_PENDING, clock=time.time):
        self._ttl, self._cap, self._clock = ttl, cap, clock
        self._items: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def __len__(self):
        return len(self._items)

    def _prune(self, now: float):
        for fid in [f for f, (_, t) in self._items.items() if now - t > self._ttl]:
            del self._items[fid]
        while len(self._items) >= self._cap:            # oldest first
            del self._items[next(iter(self._items))]

    def start(self) -> tuple[str, str]:
        """Open a sign-in. Returns (flow_id, verifier)."""
        flow_id, verifier = secrets.token_urlsafe(24), make_verifier()
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._items[flow_id] = (verifier, now)
        return flow_id, verifier

    def take(self, flow_id: str | None) -> str | None:
        """The verifier for this flow, once. None if unknown, used or expired."""
        if not flow_id:
            return None
        with self._lock:
            item = self._items.pop(flow_id, None)
        if not item:
            return None
        verifier, started = item
        return verifier if self._clock() - started <= self._ttl else None


def return_url(app_base: str, flow_id: str) -> str:
    """Where Supabase sends the sailor back to. Must be on Supabase's Redirect URLs list."""
    return f"{app_base.rstrip('/')}/?{FLOW_PARAM}={urllib.parse.quote(flow_id)}"


def authorize_url(supabase_url: str, redirect_to: str, verifier: str) -> str:
    """The Supabase address that starts Google sign-in (the same one the library builds)."""
    query = urllib.parse.urlencode({
        "provider": "google",
        "redirect_to": redirect_to,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "s256",
    })
    return f"{supabase_url.rstrip('/')}/auth/v1/authorize?{query}"


def provider_enabled(settings) -> bool:
    """True if Supabase's public /auth/v1/settings says the Google provider is switched on."""
    if not isinstance(settings, dict):
        return False
    external = settings.get("external")
    return isinstance(external, dict) and external.get("google") is True


def failure_reason(error: str | None) -> str:
    """'cancelled' when the sailor backed out on Google's page, else 'failed'."""
    return "cancelled" if error == "access_denied" else "failed"


def failure_message(reason: str, support_email: str) -> str:
    if reason == "cancelled":
        return "Google sign-in was cancelled. Tap **Continue with Google** to try again, or use your email below."
    return ("Google sign-in didn't finish. Tap **Continue with Google** to try again. "
            f"If it keeps happening, use your email below or email {support_email}.")
