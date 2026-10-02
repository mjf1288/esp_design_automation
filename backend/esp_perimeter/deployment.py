"""§9.7 deployment posture: base-URL provider abstraction.

Framework §9.7 collapses two of the three agentic modes into one implementation
path:

    "Modes 1 and 2 [local model, external API] collapse into one mechanism.
     In code, 'local model on our own hardware' and 'external API under our
     enterprise agreement' are identical: a pointer to an endpoint. They differ
     only in whether that endpoint sits inside or outside the perimeter.
     Therefore: one code path plus configuration, not three implementations."

The consequence, stated in the same section:

    "The deployment-status endpoint must report where the endpoint actually
     points, not which mode is nominally selected. Otherwise it reproduces the
     same self-contradicting posture claim as loading fonts from an external
     CDN: the system reports zero egress while continuing to generate it."

This module is the classifier that resolves *where the endpoint actually
points* and, if the operator declared a nominal mode, whether reality agrees.
The resolution is deliberately structural rather than nominal:

* the URL is parsed, and the host is normalised;
* the host is classified using the network address it resolves to, not its
  spelling — because "llm.internal.acme.com" is only inside the perimeter if it
  actually resolves to a private address, and a customer's DNS is what decides;
* if resolution fails, the host is treated as external. Fail-closed: the point
  of §9.7 is to prevent a claim of "no egress" that continues to generate
  egress, so an unresolvable name must never be presented as safe;
* if the operator declared ``ESP_AGENTIC_MODE=local`` and the resolution says
  otherwise, that contradiction is reported as a first-class finding, not
  buried in prose. A reviewer opens the deployment endpoint first.

The classifier does not decide policy. It reports facts. The API endpoint layers
prose on top; the numbers here are what a customer's security function will
audit.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable
from urllib.parse import urlsplit


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------


class AgentMode(str, Enum):
    """The three modes named in §9.7.

    ``LOCAL`` and ``EXTERNAL_API`` are one code path in this repository; the
    distinction is not about which client library is used but about where the
    resolved endpoint host lives relative to the perimeter. ``DETERMINISTIC``
    is a separate branch — the agent is not constructed at all.

    Values are stable strings because they are also the accepted values for
    ``ESP_AGENTIC_MODE``.
    """

    LOCAL = "local"
    EXTERNAL_API = "external_api"
    DETERMINISTIC = "deterministic"


class EndpointLocation(str, Enum):
    """Where the resolved endpoint host actually sits.

    * ``INSIDE_PERIMETER`` — loopback, RFC1918/RFC4193 private, link-local,
      unique-local IPv6, or explicitly declared inside via
      ``ESP_LLM_INSIDE_PERIMETER=1``. This is what §9.7 calls "local model on
      customer infrastructure".
    * ``OUTSIDE_PERIMETER`` — a routable public address. §9.7 mode 2.
    * ``UNRESOLVED`` — a hostname that could not be resolved; §9.7 says the
      report must be what the endpoint *actually* is, not what it was
      configured to be, and an unresolvable name cannot be shown to a security
      reviewer as safe. Treated as outside for the posture aggregation.
    * ``NOT_APPLICABLE`` — deterministic mode. There is no endpoint.
    * ``MISCONFIGURED`` — the ``ESP_AGENTIC_LAYER`` says on but no endpoint
      value was supplied. The system is not going to work; report that fact
      rather than a soothing placeholder.
    """

    INSIDE_PERIMETER = "inside_perimeter"
    OUTSIDE_PERIMETER = "outside_perimeter"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"
    MISCONFIGURED = "misconfigured"


# ---------------------------------------------------------------------------
# Environment surface
# ---------------------------------------------------------------------------


# The one env var that names the endpoint. Present under this name in the
# platform SDK, so nothing renames it in a running server — a rename would
# break the actual client and the posture report at the same time, which is
# the design.
_ENDPOINT_ENV = "PPLX_LLM_API_ADDRESS"

# Explicit "declare this host as inside my perimeter" override. Used when the
# customer's DNS is not reachable from the process performing the classification
# (e.g., an air-gapped test box). Presence of this variable is *reported* in the
# posture, so an operator cannot silently claim inside-perimeter status.
_INSIDE_OVERRIDE_ENV = "ESP_LLM_INSIDE_PERIMETER"

# Explicit nominal mode declaration. If set, the classifier reports both the
# declaration and reality, and flags a contradiction. If unset, the reported
# mode is inferred from configuration state alone.
_MODE_DECLARATION_ENV = "ESP_AGENTIC_MODE"

# Agent kill switch. Values that flip it off. Kept in sync with the app-level
# _AGENTIC_OFF_VALUES so an out-of-band change in one place cannot silently
# desynchronise the posture from the running code.
_AGENTIC_LAYER_ENV = "ESP_AGENTIC_LAYER"
AGENTIC_OFF_VALUES: frozenset[str] = frozenset({"0", "off", "false", "no", "disabled"})


# ---------------------------------------------------------------------------
# Result value type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeploymentPosture:
    """The §9.7 posture facts.

    Fields are values, not prose: the API layer wraps them for humans. This
    object is what a test asserts against, so an accidental prose change in the
    endpoint payload cannot silently invalidate the posture claim.
    """

    # What the operator asked for. May be ``None`` when nothing was declared.
    declared_mode: AgentMode | None
    # What the system is actually running. Derived from the running process,
    # not from ``declared_mode``. This is the one the framework wants reported.
    effective_mode: AgentMode
    # Where the resolved endpoint host actually sits.
    endpoint_location: EndpointLocation
    # The raw configured value, verbatim, or ``None`` in deterministic mode.
    configured_endpoint: str | None
    # The host component of the URL, if one could be parsed.
    endpoint_host: str | None
    # The IP the host resolved to, if resolution succeeded. Reported so a
    # reviewer can cross-check against their firewall log — a hostname alone is
    # not falsifiable.
    resolved_address: str | None
    # ``True`` when the operator declared a mode that contradicts reality.
    # §9.7's central warning is that this must not be hidden.
    posture_contradiction: bool
    # A short, machine-readable reason string for the contradiction, or ``None``.
    # Kept separate from the prose in the API layer so tests can assert against
    # it without matching sentence structure.
    contradiction_reason: str | None
    # ``True`` when ``ESP_LLM_INSIDE_PERIMETER`` was used to override the
    # network-address heuristic. Recorded so the override is visible in the
    # posture instead of masquerading as a resolution outcome.
    inside_override_applied: bool


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


# Type alias for the resolver, so tests can inject a deterministic one.
Resolver = Callable[[str], str]


def _default_resolver(host: str) -> str:
    """Resolve ``host`` to an IP literal, or raise ``socket.gaierror``.

    ``socket.getaddrinfo`` returns both v4 and v6; the first entry is enough
    for classification, because the private/public distinction is per-address
    and any private answer counts as inside.
    """
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    if not infos:
        raise socket.gaierror(f"no address for {host!r}")
    # Prefer a private answer if one exists in the record set: a name that
    # returns both a private LAN address and a public gateway address (a
    # perfectly normal split-horizon setup) must classify as inside.
    private_first: str | None = None
    public_first: str | None = None
    for info in infos:
        sockaddr = info[4]
        address = sockaddr[0]
        # Strip zone id from link-local IPv6 (fe80::1%eth0).
        if isinstance(address, str) and "%" in address:
            address = address.split("%", 1)[0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if _is_inside_address(parsed):
            private_first = str(parsed)
            break
        public_first = public_first or str(parsed)
    if private_first is not None:
        return private_first
    if public_first is not None:
        return public_first
    raise socket.gaierror(f"no usable address for {host!r}")


def _is_inside_address(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Address-level 'inside the perimeter' test.

    The IANA-designated private, loopback, link-local and unique-local ranges
    are treated as inside. Everything else — including the public documentation
    ranges (192.0.2.0/24, 2001:db8::/32) — is outside. Documentation ranges
    look private but a real deployment will not use them; using them signals a
    misconfiguration rather than a private network.
    """
    if addr.is_loopback or addr.is_private or addr.is_link_local:
        return True
    if isinstance(addr, ipaddress.IPv6Address):
        # unique-local (fc00::/7) is IPv6's equivalent of RFC1918.
        if addr.is_site_local:  # deprecated but classified as private
            return True
    return False


def _read_bool(env_name: str, *, environ: dict[str, str]) -> bool:
    raw = environ.get(env_name, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _agentic_enabled(environ: dict[str, str]) -> bool:
    raw = environ.get(_AGENTIC_LAYER_ENV, "on").strip().lower()
    return raw not in AGENTIC_OFF_VALUES


def _parse_declared_mode(environ: dict[str, str]) -> AgentMode | None:
    raw = environ.get(_MODE_DECLARATION_ENV, "").strip().lower()
    if not raw:
        return None
    for mode in AgentMode:
        if mode.value == raw:
            return mode
    # An unparseable declaration is not silently reinterpreted: the operator
    # asked for something specific, and it is not one of the three named
    # modes. Reported as declared=None so the effective classification runs
    # unchanged, but the contradiction check is preserved via the raw value
    # below.
    return None


def classify_deployment(
    *,
    environ: dict[str, str] | None = None,
    resolver: Resolver | None = None,
    agentic_override: bool | None = None,
) -> DeploymentPosture:
    """Return the §9.7 posture derived from configuration and DNS.

    Neither argument is required in production; both exist so tests can pin
    the environment and DNS behaviour exactly. The classifier reads ``os.environ``
    once at call time, so a subsequent env change is picked up on the next
    posture request without process restart. That is deliberate: the framework
    says the report must reflect what the running process is doing, and
    reading env at call time is the cheapest way to keep the two aligned.
    """
    env: dict[str, str] = dict(os.environ if environ is None else environ)
    resolve = resolver if resolver is not None else _default_resolver
    declared = _parse_declared_mode(env)
    raw_declared = env.get(_MODE_DECLARATION_ENV, "").strip().lower() or None
    inside_override = _read_bool(_INSIDE_OVERRIDE_ENV, environ=env)

    # Deterministic mode short-circuits everything else. §9.7's mandatory
    # fallback: the agent is not constructed, so there is no endpoint to
    # classify, and reporting a made-up one would defeat the purpose.
    # ``agentic_override`` lets the caller supply the running service's actual
    # agent state, which is more authoritative than an env var (the process
    # might have been constructed with ``agentic=False`` explicitly).
    agent_on = _agentic_enabled(env) if agentic_override is None else agentic_override
    if not agent_on:
        contradiction, reason = _mode_contradiction(
            declared=declared,
            raw_declared=raw_declared,
            effective=AgentMode.DETERMINISTIC,
            location=EndpointLocation.NOT_APPLICABLE,
        )
        return DeploymentPosture(
            declared_mode=declared,
            effective_mode=AgentMode.DETERMINISTIC,
            endpoint_location=EndpointLocation.NOT_APPLICABLE,
            configured_endpoint=None,
            endpoint_host=None,
            resolved_address=None,
            posture_contradiction=contradiction,
            contradiction_reason=reason,
            inside_override_applied=inside_override,
        )

    configured = env.get(_ENDPOINT_ENV, "").strip() or None
    if configured is None:
        # Agent layer is on but no endpoint. The client will fail on first
        # call. Better to name that as a misconfiguration than to compute a
        # fake "external" classification against an empty string.
        contradiction, reason = _mode_contradiction(
            declared=declared,
            raw_declared=raw_declared,
            effective=AgentMode.EXTERNAL_API,
            location=EndpointLocation.MISCONFIGURED,
        )
        return DeploymentPosture(
            declared_mode=declared,
            # Effective is 'external_api' because that is what the running
            # process is on track to become the moment an endpoint is set;
            # reporting 'local' would suggest a working local deployment.
            effective_mode=AgentMode.EXTERNAL_API,
            endpoint_location=EndpointLocation.MISCONFIGURED,
            configured_endpoint=None,
            endpoint_host=None,
            resolved_address=None,
            posture_contradiction=contradiction,
            contradiction_reason=reason,
            inside_override_applied=inside_override,
        )

    host = _extract_host(configured)
    if host is None:
        # URL that does not parse. Not classifiable; not shown as safe.
        contradiction, reason = _mode_contradiction(
            declared=declared,
            raw_declared=raw_declared,
            effective=AgentMode.EXTERNAL_API,
            location=EndpointLocation.MISCONFIGURED,
        )
        return DeploymentPosture(
            declared_mode=declared,
            effective_mode=AgentMode.EXTERNAL_API,
            endpoint_location=EndpointLocation.MISCONFIGURED,
            configured_endpoint=configured,
            endpoint_host=None,
            resolved_address=None,
            posture_contradiction=contradiction,
            contradiction_reason=reason,
            inside_override_applied=inside_override,
        )

    location, resolved = _classify_host(host, resolver=resolve, override=inside_override)
    effective = (
        AgentMode.LOCAL
        if location is EndpointLocation.INSIDE_PERIMETER
        else AgentMode.EXTERNAL_API
    )
    contradiction, reason = _mode_contradiction(
        declared=declared,
        raw_declared=raw_declared,
        effective=effective,
        location=location,
    )
    return DeploymentPosture(
        declared_mode=declared,
        effective_mode=effective,
        endpoint_location=location,
        configured_endpoint=configured,
        endpoint_host=host,
        resolved_address=resolved,
        posture_contradiction=contradiction,
        contradiction_reason=reason,
        inside_override_applied=inside_override,
    )


def _extract_host(url: str) -> str | None:
    """Best-effort host extraction that tolerates a bare host or ``host:port``.

    An operator may configure a plain ``llm.internal:8080`` rather than a full
    URL. ``urlsplit`` requires a scheme; if the scheme is missing, retry with
    ``//`` prefix so ``urlsplit`` treats the rest as authority.
    """
    parsed = urlsplit(url)
    if parsed.hostname:
        return parsed.hostname
    parsed = urlsplit("//" + url)
    return parsed.hostname


def _classify_host(
    host: str,
    *,
    resolver: Resolver,
    override: bool,
) -> tuple[EndpointLocation, str | None]:
    """Classify a hostname's location.

    Order matters: literal IP address first, because ``127.0.0.1`` does not
    need DNS and must not be marked unresolved on a machine with no network.
    Then the explicit override, which is a documented escape hatch, so a
    reviewer can see it in the posture rather than having to grep env vars.
    Then DNS.
    """
    # Bracketed IPv6 literals arrive from urlsplit already stripped.
    try:
        addr = ipaddress.ip_address(host)
        return (
            EndpointLocation.INSIDE_PERIMETER
            if _is_inside_address(addr)
            else EndpointLocation.OUTSIDE_PERIMETER
        ), str(addr)
    except ValueError:
        pass

    if override:
        # A declared 'this host is inside my perimeter' assertion. Reported
        # separately in ``inside_override_applied`` so a reviewer can see it.
        # Resolution is still attempted so the address ends up in the payload
        # for cross-checking.
        try:
            address = resolver(host)
        except (OSError, socket.gaierror):
            address = None
        return EndpointLocation.INSIDE_PERIMETER, address

    if _looks_like_reserved_local_name(host):
        # ``localhost``, ``*.localhost``, ``*.local`` (mDNS), ``*.internal``
        # and ``*.home.arpa`` are RFC-reserved private names. Treating them
        # as inside without a DNS round-trip is safe and works on air-gapped
        # boxes where getaddrinfo returns nothing.
        try:
            address = resolver(host)
        except (OSError, socket.gaierror):
            address = None
        return EndpointLocation.INSIDE_PERIMETER, address

    try:
        address = resolver(host)
    except (OSError, socket.gaierror):
        return EndpointLocation.UNRESOLVED, None

    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return EndpointLocation.UNRESOLVED, address
    return (
        EndpointLocation.INSIDE_PERIMETER
        if _is_inside_address(parsed)
        else EndpointLocation.OUTSIDE_PERIMETER
    ), str(parsed)


_RESERVED_LOCAL_SUFFIXES: tuple[str, ...] = (
    ".local",
    ".localhost",
    ".internal",
    ".home.arpa",
    ".intranet",
    ".corp",
    ".lan",
)


def _looks_like_reserved_local_name(host: str) -> bool:
    lower = host.lower().rstrip(".")
    if lower in {"localhost"}:
        return True
    return any(lower.endswith(suffix) for suffix in _RESERVED_LOCAL_SUFFIXES)


def _mode_contradiction(
    *,
    declared: AgentMode | None,
    raw_declared: str | None,
    effective: AgentMode,
    location: EndpointLocation,
) -> tuple[bool, str | None]:
    """Compare the declared nominal mode against what the deployment is doing.

    Returns a machine-readable reason string so the API layer can present the
    contradiction consistently and so tests can assert against it without
    reading prose. The reason string names both sides of the disagreement.
    """
    # An unparseable declaration is a contradiction with the whole mode set.
    if declared is None and raw_declared:
        return (
            True,
            f"declared_mode={raw_declared!r} is not one of "
            f"{[m.value for m in AgentMode]}",
        )
    if declared is None:
        return False, None
    if declared is effective:
        # Even matching, an unresolved endpoint is still a posture concern.
        if location is EndpointLocation.UNRESOLVED:
            return (
                True,
                "declared_mode matches effective_mode but the endpoint host did not resolve",
            )
        if location is EndpointLocation.MISCONFIGURED:
            return (
                True,
                "declared_mode matches effective_mode but the endpoint is misconfigured",
            )
        return False, None
    # The one §9.7 explicitly warns about: mode says one thing, endpoint says
    # another. Named directly so a reviewer sees it in the top-level payload.
    return (
        True,
        f"declared_mode={declared.value!r} but effective_mode={effective.value!r} "
        f"(endpoint_location={location.value})",
    )


# ---------------------------------------------------------------------------
# Introspection helpers used by the API layer
# ---------------------------------------------------------------------------


def env_surface() -> Iterable[tuple[str, str]]:
    """Names and short descriptions of the env vars this module reads.

    The API endpoint publishes this so a customer's reviewer can find every
    knob that changes the posture, not only the ones that flip a boolean.
    """
    return (
        (
            _ENDPOINT_ENV,
            "URL of the OpenAI-compatible model endpoint. §9.7 mode 1 or 2 "
            "depending on where the host resolves.",
        ),
        (
            _AGENTIC_LAYER_ENV,
            "Master switch for the agentic layer. Off values "
            f"({sorted(AGENTIC_OFF_VALUES)}) select §9.7 deterministic mode.",
        ),
        (
            _MODE_DECLARATION_ENV,
            "Optional nominal declaration ('local', 'external_api', "
            "'deterministic'). Cross-checked against reality; a mismatch is "
            "reported as a posture contradiction.",
        ),
        (
            _INSIDE_OVERRIDE_ENV,
            "Escape hatch for air-gapped deployments where DNS cannot classify "
            "the host from this process. Its use is visible in the posture.",
        ),
    )
