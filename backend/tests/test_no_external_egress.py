"""Framework §9.7: the browser must not reach outside the customer perimeter.

The deployment posture endpoint reports whether the installation makes external
network calls. That report is only worth reading if it covers the whole
application, and the obvious blind spot is the frontend: a single
``<link href="https://fonts.googleapis.com/...">`` means every engineer who
opens a design hands a third party their client IP, user agent, and a
timestamp, while ``/api/deployment`` still cheerfully reports zero egress
because nothing on the Python side made a call.

That is worse than an ordinary bug. An operator who deploys this inside their
perimeter on the strength of the posture report has been told something untrue.
So the check lives in the test suite rather than in a code review checklist.

Two layers:

  1. Source-level, always runs. ``frontend/index.html`` is in the repository, so
     the most likely regression -- someone pasting a Google Fonts snippet back
     in -- is caught without needing a build.
  2. Bundle-level, runs when ``frontend/dist`` exists. Inspects what the browser
     would actually fetch: document subresources, CSS ``url()`` targets, and
     ``@font-face`` sources.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
DIST = FRONTEND / "dist"

# Hosts that must never appear anywhere in the shipped frontend, including in
# comments, strings, and source maps. These are the ones whose presence implies
# a live third-party dependency at runtime rather than an incidental mention.
FORBIDDEN_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "ajax.googleapis.com",
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "segment.io",
    "segment.com",
    "sentry.io",
    "bugsnag.com",
    "datadoghq.com",
    "hotjar.com",
    "mixpanel.com",
    "intercom.io",
    "posthog.com",
    "amplitude.com",
    "fullstory.com",
    "launchdarkly.com",
    "gravatar.com",
    "use.typekit.net",
    "kit.fontawesome.com",
)

# Absolute URLs the browser will never dereference as a network request.
# Whitelisted by exact prefix, with the reason, so the list cannot quietly grow
# into a loophole.
BENIGN_ABSOLUTE_URLS = {
    # XML namespace identifier on every <svg> element. Namespaces are compared
    # as opaque strings; no user agent fetches them.
    "http://www.w3.org/",
    # The dev-mode API base fallback in src/stores.ts. Same machine by
    # definition, and it is replaced by the injected port at deploy time.
    "http://localhost",
    "http://127.0.0.1",
}

# Absolute URLs that appear only inside diagnostic message strings shipped by
# Vue and Chart.js ("see https://vuejs.org/..."). They are documentation
# pointers in error text, never fetched. Matched as substrings of the file
# contents, not as fetchable references.
BENIGN_DOC_HOSTS = (
    "vuejs.org",
    "chartjs.org",
    "github.com",
    "developer.mozilla.org",
    "stackoverflow.com",
    "en.wikipedia.org",
    "robertpenner.com",
    "scaledinnovation.com",
    "whatismybrowser.com",
)

_URL_RE = re.compile(r"https?://[^\s'\"()<>\\]+")


def _is_benign(url: str) -> bool:
    if any(url.startswith(prefix) for prefix in BENIGN_ABSOLUTE_URLS):
        return True
    host = url.split("//", 1)[1].split("/", 1)[0].split(":", 1)[0]
    return any(host == h or host.endswith("." + h) for h in BENIGN_DOC_HOSTS)


# =============================================================================
# Layer 1 -- source, always runs
# =============================================================================


def test_index_html_source_has_no_external_references() -> None:
    html = (FRONTEND / "index.html").read_text()
    offenders = [u for u in _URL_RE.findall(html) if not _is_benign(u)]
    assert not offenders, (
        "frontend/index.html references external hosts, which breaks the §9.7 "
        f"on-premise posture: {offenders}. Self-host the resource instead; see "
        "frontend/src/fonts.css for how the typefaces are bundled."
    )


def test_index_html_source_has_no_external_connection_hints() -> None:
    """``preconnect``/``dns-prefetch`` open a connection before any resource is
    requested, so they leak even when the resource they were warming is gone.
    Removing the stylesheet but leaving the preconnect is a realistic
    half-finished cleanup, and it still emits a DNS query and a TLS handshake."""
    html = (FRONTEND / "index.html").read_text()
    hints = re.findall(
        r"""<link[^>]*rel=["'](?:preconnect|dns-prefetch|prefetch|preload|modulepreload)["'][^>]*>""",
        html,
        re.IGNORECASE,
    )
    for hint in hints:
        for url in _URL_RE.findall(hint):
            assert _is_benign(url), f"external connection hint remains: {hint}"


def test_fonts_are_declared_as_a_bundled_dependency() -> None:
    """A self-hosting claim is only true if the font files are actually a build
    input. If someone removes the @fontsource dependency, the @font-face rules
    resolve to nothing and the console silently falls back to system fonts --
    which looks like a styling bug, not a perimeter regression, so it would be
    fixed by re-adding the Google Fonts link."""
    package_json = (FRONTEND / "package.json").read_text()
    assert "@fontsource/inter" in package_json
    assert "@fontsource/jetbrains-mono" in package_json

    fonts_css = (FRONTEND / "src" / "fonts.css").read_text()
    for family in ("@fontsource/inter/latin-", "@fontsource/jetbrains-mono/latin-"):
        assert family in fonts_css

    main_ts = (FRONTEND / "src" / "main.ts").read_text()
    assert "./fonts.css" in main_ts, "fonts.css is never imported, so it is dead"


# =============================================================================
# Layer 2 -- built bundle
# =============================================================================


def _dist_files() -> list[Path]:
    return [p for p in DIST.rglob("*") if p.is_file()]


def _require_dist() -> None:
    if not DIST.is_dir():
        pytest.skip(
            "frontend/dist not built; run `npm run build` in frontend/ to let "
            "this §9.7 egress guard actually inspect the shipped bundle"
        )


def test_bundle_contains_no_forbidden_host() -> None:
    """Blanket denylist. Applies to source maps too: a map is served to the
    browser on request and is part of what the customer receives."""
    _require_dist()
    hits: list[str] = []
    for path in _dist_files():
        try:
            text = path.read_text(errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        for host in FORBIDDEN_HOSTS:
            if host in text:
                hits.append(f"{path.relative_to(DIST)} -> {host}")
    assert not hits, f"forbidden third-party hosts in the built bundle: {hits}"


def test_bundle_html_subresources_are_all_same_origin() -> None:
    _require_dist()
    html = (DIST / "index.html").read_text()
    refs = re.findall(r"""(?:href|src)=["']([^"']+)["']""", html)
    assert refs, "index.html has no subresources at all, which means it is broken"
    for ref in refs:
        if ref.startswith(("http://", "https://", "//")):
            assert _is_benign(ref), f"index.html loads {ref} from outside the perimeter"


def test_bundle_css_urls_are_all_relative() -> None:
    """Covers the @font-face src rules specifically: this is the path a font
    regression would take once the <link> is gone."""
    _require_dist()
    css_files = [p for p in _dist_files() if p.suffix == ".css"]
    assert css_files, "no CSS emitted; the build did not produce a usable bundle"
    for path in css_files:
        for url in re.findall(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", path.read_text()):
            if url.startswith("data:"):
                continue
            assert not url.startswith(("http://", "https://", "//")), (
                f"{path.relative_to(DIST)} fetches {url} from outside the perimeter"
            )


def test_bundle_actually_ships_the_font_files() -> None:
    """The complement of the negative checks: proving nothing external is
    referenced is not the same as proving the fonts are present. Without this,
    deleting fonts.css entirely would make every egress test pass."""
    _require_dist()
    names = [p.name for p in _dist_files()]
    woff2 = [n for n in names if n.endswith(".woff2")]
    assert any("inter" in n.lower() for n in woff2), "no Inter woff2 in the bundle"
    assert any("jetbrains" in n.lower() for n in woff2), (
        "no JetBrains Mono woff2 in the bundle"
    )

    css = "\n".join(
        p.read_text() for p in _dist_files() if p.suffix == ".css"
    )
    assert "@font-face" in css
    for weight in ("400", "500", "600", "700"):
        assert weight in css, f"Inter {weight} is used by style.css but not bundled"
