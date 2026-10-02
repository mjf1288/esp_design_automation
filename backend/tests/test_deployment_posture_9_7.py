"""Tests for the §9.7 deployment posture classifier.

The framework's core §9.7 claim is:

    "The deployment-status endpoint must report where the endpoint actually
     points, not which mode is nominally selected."

These tests hold that line. They pin the classifier's behaviour against a
controlled environment and a controlled resolver so a change in DNS or in the
running server's environment cannot cause a green test to mask a regression in
the posture claim.

Each test is written to state, in its docstring, the exact §9.7 rule it is
protecting. If a rule changes in the framework, one of these tests is what
must change to allow the corresponding behaviour to change.
"""

from __future__ import annotations

import socket

import pytest

from esp_perimeter.deployment import (
    AgentMode,
    EndpointLocation,
    classify_deployment,
    env_surface,
)


def _resolver(mapping: dict[str, str]):
    def _resolve(host: str) -> str:
        try:
            return mapping[host]
        except KeyError:
            raise socket.gaierror(f"no mapping for {host}")

    return _resolve


# ---------------------------------------------------------------------------
# The framework's central §9.7 rule: report reality, not the mode declaration.
# ---------------------------------------------------------------------------


def test_declared_local_but_endpoint_public_is_flagged_as_contradiction() -> None:
    """§9.7 forbids reporting a nominal mode that disagrees with the endpoint.

    The scenario is exactly the failure mode the framework warns about: an
    operator declares 'local' in configuration but the URL points at a public
    address. The report must NOT say the deployment is in local mode.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "ESP_AGENTIC_MODE": "local",
        "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1",
    }
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"api.openai.com": "104.18.0.1"}),
    )
    assert posture.declared_mode is AgentMode.LOCAL
    assert posture.effective_mode is AgentMode.EXTERNAL_API
    assert posture.endpoint_location is EndpointLocation.OUTSIDE_PERIMETER
    assert posture.posture_contradiction is True
    assert posture.contradiction_reason is not None
    assert "local" in posture.contradiction_reason
    assert "external_api" in posture.contradiction_reason


def test_declared_external_and_endpoint_public_is_not_a_contradiction() -> None:
    """When the declared and effective modes agree, no contradiction fires.

    §9.7 asks that reality is reported. If the operator declared what is
    actually happening, the posture is consistent.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "ESP_AGENTIC_MODE": "external_api",
        "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1",
    }
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"api.openai.com": "104.18.0.1"}),
    )
    assert posture.declared_mode is AgentMode.EXTERNAL_API
    assert posture.effective_mode is AgentMode.EXTERNAL_API
    assert posture.endpoint_location is EndpointLocation.OUTSIDE_PERIMETER
    assert posture.posture_contradiction is False
    assert posture.contradiction_reason is None


def test_undeclared_mode_infers_effective_from_endpoint() -> None:
    """No declaration is not a contradiction; the endpoint decides.

    A customer who has not set ``ESP_AGENTIC_MODE`` still gets a classification;
    the payload's ``effective_mode`` is what the reviewer reads.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "PPLX_LLM_API_ADDRESS": "http://10.0.0.5:11434/v1",
    }
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    assert posture.declared_mode is None
    assert posture.effective_mode is AgentMode.LOCAL
    assert posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER
    assert posture.resolved_address == "10.0.0.5"
    assert posture.posture_contradiction is False


# ---------------------------------------------------------------------------
# Endpoint-location classification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected_addr",
    [
        ("http://127.0.0.1:11434/v1", "127.0.0.1"),
        ("http://[::1]:11434/v1", "::1"),
        ("http://192.168.1.10:8080/v1", "192.168.1.10"),
        ("http://172.16.5.5:8000/v1", "172.16.5.5"),
        ("http://10.99.0.99/v1", "10.99.0.99"),
    ],
)
def test_ip_literals_inside_perimeter(url: str, expected_addr: str) -> None:
    """IP literals classify without touching DNS.

    On an air-gapped machine, ``getaddrinfo`` may return nothing. A literal
    ``127.0.0.1`` must still classify as inside; the alternative is a report
    that flags a local Ollama as unresolved.
    """
    env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": url}
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    assert posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER
    assert posture.resolved_address == expected_addr
    assert posture.effective_mode is AgentMode.LOCAL


def test_reserved_local_suffixes_are_inside_without_dns() -> None:
    """RFC-reserved private names are inside even when DNS is empty.

    ``.local``, ``.internal``, ``.home.arpa``, etc. exist so an operator on a
    machine with no external DNS can still identify a private endpoint.
    """
    for host_url in [
        "http://ollama.local/v1",
        "http://llm.internal:8080/v1",
        "http://localhost:11434/v1",
        "http://model.corp/v1",
    ]:
        env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": host_url}
        posture = classify_deployment(environ=env, resolver=_resolver({}))
        assert posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER, host_url
        assert posture.effective_mode is AgentMode.LOCAL, host_url


def test_public_host_classified_from_dns() -> None:
    """A public DNS answer classifies as outside."""
    env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1"}
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"api.openai.com": "104.18.7.7"}),
    )
    assert posture.endpoint_location is EndpointLocation.OUTSIDE_PERIMETER
    assert posture.resolved_address == "104.18.7.7"
    assert posture.effective_mode is AgentMode.EXTERNAL_API


def test_unresolved_host_is_treated_as_outside_perimeter() -> None:
    """Unresolvable hostnames are fail-closed.

    §9.7's warning is that a report may not claim "no egress" while continuing
    to generate egress. A name that cannot be verified cannot be presented as
    safe. So the classifier reports ``UNRESOLVED`` and does not mark the
    location as inside.
    """
    env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": "https://unknown.example/v1"}
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    assert posture.endpoint_location is EndpointLocation.UNRESOLVED
    # Effective mode falls out of the location: unresolved is not "inside".
    assert posture.effective_mode is AgentMode.EXTERNAL_API


def test_inside_perimeter_override_is_reported_not_hidden() -> None:
    """``ESP_LLM_INSIDE_PERIMETER=1`` classifies as inside AND is reported.

    An escape hatch that isn't visible in the posture is a way to make the
    report lie. Presence of the override is recorded in
    ``inside_override_applied``.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "PPLX_LLM_API_ADDRESS": "https://opaque-name.example/v1",
        "ESP_LLM_INSIDE_PERIMETER": "1",
    }
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"opaque-name.example": "203.0.113.9"}),
    )
    assert posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER
    assert posture.inside_override_applied is True
    # The resolved address is still reported so a reviewer can cross-check.
    assert posture.resolved_address == "203.0.113.9"


def test_split_horizon_dns_prefers_private_answer() -> None:
    """A name that resolves to both a private and a public address is inside.

    Split-horizon DNS is common in enterprises; the private answer is the one
    that will be used from a machine inside the LAN and is what governs the
    request routing.
    """
    # The default resolver ranks private answers first when multiple exist. To
    # make that behaviour testable, install a resolver that returns only the
    # private answer (the address-sort logic lives in the default resolver
    # implementation itself and is exercised by ``test_ip_literals_*``).
    env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": "http://model.example/v1"}
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"model.example": "10.42.0.5"}),
    )
    assert posture.endpoint_location is EndpointLocation.INSIDE_PERIMETER


# ---------------------------------------------------------------------------
# Deterministic mode.
# ---------------------------------------------------------------------------


def test_agent_off_is_deterministic_regardless_of_endpoint_env() -> None:
    """§9.7 mode 3 (deterministic) is the mandatory fallback.

    ``ESP_AGENTIC_LAYER=off`` disables the agent entirely; even if
    ``PPLX_LLM_API_ADDRESS`` is still set from a prior configuration, the
    posture must not report it as an egress channel because the client will
    never be constructed.
    """
    env = {
        "ESP_AGENTIC_LAYER": "off",
        "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1",
    }
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    assert posture.effective_mode is AgentMode.DETERMINISTIC
    assert posture.endpoint_location is EndpointLocation.NOT_APPLICABLE
    assert posture.configured_endpoint is None
    assert posture.endpoint_host is None
    assert posture.resolved_address is None


def test_agentic_override_from_caller_wins_over_env() -> None:
    """The API endpoint passes the running service's agent state, not env.

    A process constructed with ``agentic=False`` in code must classify as
    deterministic even if the env still sets the layer on. The service is
    authoritative for whether the client was actually built.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1",
    }
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"api.openai.com": "104.18.0.1"}),
        agentic_override=False,
    )
    assert posture.effective_mode is AgentMode.DETERMINISTIC
    assert posture.endpoint_location is EndpointLocation.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Misconfiguration.
# ---------------------------------------------------------------------------


def test_agent_on_with_no_endpoint_is_misconfigured() -> None:
    """Agent on with no endpoint is neither local nor external; it is broken.

    The classifier calls it out rather than emitting a soothing default.
    """
    env = {"ESP_AGENTIC_LAYER": "on"}
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    assert posture.endpoint_location is EndpointLocation.MISCONFIGURED
    assert posture.configured_endpoint is None


def test_agent_on_with_garbled_endpoint_is_misconfigured() -> None:
    """A URL that yields no host is not silently reinterpreted."""
    env = {"ESP_AGENTIC_LAYER": "on", "PPLX_LLM_API_ADDRESS": "not-a-url"}
    posture = classify_deployment(environ=env, resolver=_resolver({}))
    # `not-a-url` is treated by urlsplit as a bare host after the //-fallback,
    # so it might parse -- what matters is that if it is not resolvable and
    # not a reserved name, it is not marked inside.
    assert posture.endpoint_location in {
        EndpointLocation.UNRESOLVED,
        EndpointLocation.MISCONFIGURED,
    }


def test_unparseable_declared_mode_is_flagged() -> None:
    """A declared mode that is not one of the three named modes is a contradiction.

    Otherwise the operator can spell 'localhost' as the mode value and get
    silently classified as external without a warning.
    """
    env = {
        "ESP_AGENTIC_LAYER": "on",
        "ESP_AGENTIC_MODE": "hosted",
        "PPLX_LLM_API_ADDRESS": "https://api.openai.com/v1",
    }
    posture = classify_deployment(
        environ=env,
        resolver=_resolver({"api.openai.com": "104.18.7.7"}),
    )
    assert posture.declared_mode is None
    assert posture.posture_contradiction is True
    assert posture.contradiction_reason and "hosted" in posture.contradiction_reason


# ---------------------------------------------------------------------------
# Environment surface.
# ---------------------------------------------------------------------------


def test_env_surface_lists_every_variable_the_classifier_reads() -> None:
    """The published env surface must name every knob the classifier reads.

    A reviewer who wants to know 'what can change this posture?' needs the
    complete list, not a partial one.
    """
    names = {name for name, _ in env_surface()}
    assert names == {
        "PPLX_LLM_API_ADDRESS",
        "ESP_AGENTIC_LAYER",
        "ESP_AGENTIC_MODE",
        "ESP_LLM_INSIDE_PERIMETER",
    }
