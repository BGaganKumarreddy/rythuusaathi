"""Isolated smoke test for Google Gemini 3.8 Flash structured inference.

Requirements:
- Reuse existing BYOK credential lookup (LocalAuthentication + CredentialService + WindowsCredentialStore).
- Reuse real GeminiProvider adapter.
- Send exactly one small structured request (IntakeInterpretation).
- Use existing model (gemini-3.8-flash) and credentials.
- Do not invoke the six-agent workflow.
- Do not expose credentials.
- Print sanitized diagnostics, HTTP status, latency, finish reason, token usage, and validation result.
- Exit nonzero on failure.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import getpass
import os
from pathlib import Path
import socket
import ssl
import sys
import time
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_framework import Message

from rythusaathi.agents.llm import (
    AuthenticatedGeminiInference,
    IntakeInterpretation,
    call_structured,
    make_json_request,
)
from rythusaathi.agents.workflow import _INTAKE_PROMPT
from rythusaathi.credentials import (
    CredentialService,
    CredentialUnavailable,
    WindowsCredentialStore,
)
from rythusaathi.inference.contracts import (
    InferenceDiagnostic,
    InferenceDiagnosticCode,
    InferenceRequest,
    InferenceStage,
    InferenceTokenUsage,
    ProviderFailure,
)
from rythusaathi.inference.providers.gemini import (
    DEFAULT_MODEL_ID,
    GEMINI_BASE_URL,
    GeminiProvider,
    GeminiProviderConfig,
)
from rythusaathi.inference.service import AuthenticatedGeminiService
from rythusaathi.local_auth import LocalAuthentication


async def run_gemini_smoke(
    inference: AuthenticatedGeminiInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    query: str = "My cotton crop in Warangal has leaf spot",
) -> tuple[bool, IntakeInterpretation | None, InferenceDiagnostic | None, InferenceTokenUsage | None, float]:
    """Execute exactly one structured inference request via the real Gemini provider adapter.

    Returns:
        (success, interpretation, diagnostic, usage, elapsed_seconds)
    """
    request = make_json_request(
        system_prompt=_INTAKE_PROMPT,
        payload={"query": query},
        response_model=IntakeInterpretation,
        agent_name="intake",
        max_output_tokens=1024,
    )
    start_time = time.monotonic()
    try:
        result = await call_structured(
            inference,
            request,
            expected_user_id=expected_user_id,
            expected_tenant_id=expected_tenant_id,
            response_model=IntakeInterpretation,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        usage = inference.usage_records[-1] if inference.usage_records else None
        return True, result, None, usage, elapsed
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        usage = inference.usage_records[-1] if inference.usage_records else None
        return False, None, diag, usage, elapsed
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        return False, None, diag, None, elapsed


@dataclass(frozen=True)
class ExperimentResult:
    """Standardized result recorded for each Gemini live inference experiment."""

    experiment_name: str
    model_id: str
    endpoint_path: str
    success: bool
    http_status: int | None
    elapsed_seconds: float
    headers_received: bool
    body_bytes_received: bool
    response_text: str | None = None
    interpretation: IntakeInterpretation | None = None
    diagnostic: InferenceDiagnostic | None = None
    usage: InferenceTokenUsage | None = None
    exception_type_chain: str | None = None
    provider_error_code: int | str | None = None
    provider_error_status: str | None = None
    provider_error_message: str | None = None
    retry_after_seconds: float | None = None
    request_id: str | None = None
    trace_id: str | None = None
    sanitized_headers: dict[str, str] | None = None


async def run_gemini_minimal_probe(
    inference: AuthenticatedGeminiInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    prompt: str = "Respond with OK.",
    max_output_tokens: int = 16,
) -> ExperimentResult:
    """Send a minimal non-streaming chat-completion request using the authenticated adapter."""
    request = InferenceRequest(
        messages=[Message("user", [prompt])],
        options={"max_tokens": max_output_tokens, "temperature": 0},
        agent_name="minimal_probe",
    )
    model_id = inference.model_id or DEFAULT_MODEL_ID
    endpoint_path = "/v1beta/openai/chat/completions"
    start_time = time.monotonic()
    try:
        provider_resp = await inference.complete(
            request,
            expected_user_id=expected_user_id,
            expected_tenant_id=expected_tenant_id,
        )
        elapsed = max(0.0, time.monotonic() - start_time)
        usage = inference.usage_records[-1] if inference.usage_records else None
        resp_text = ""
        if provider_resp.response and provider_resp.response.messages:
            for m in provider_resp.response.messages:
                if m.role == "assistant":
                    resp_text += "".join(
                        item if isinstance(item, str) else getattr(item, "text", "")
                        for item in m.contents
                    ).strip()
        http_status = usage.http_status if usage and usage.http_status else 200
        return ExperimentResult(
            experiment_name="1. Minimal Chat Completion Probe",
            model_id=model_id,
            endpoint_path=endpoint_path,
            success=True,
            http_status=http_status,
            elapsed_seconds=elapsed,
            headers_received=True,
            body_bytes_received=bool(resp_text),
            response_text=resp_text,
            interpretation=None,
            diagnostic=None,
            usage=usage,
            exception_type_chain=None,
            provider_error_code=None,
            provider_error_status=None,
            provider_error_message=None,
            retry_after_seconds=None,
            request_id=getattr(usage, "request_id", None) if usage else None,
            trace_id=None,
            sanitized_headers=None,
        )
    except ProviderFailure as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = exc.diagnostic
        http_status = getattr(diag, "http_status", None) if diag else None
        headers_recvd = http_status is not None
        body_recvd = bool(
            getattr(diag, "provider_error_status", None)
            or getattr(diag, "provider_error_message", None)
            or getattr(diag, "provider_error_code", None) is not None
        ) if diag else False
        return ExperimentResult(
            experiment_name="1. Minimal Chat Completion Probe",
            model_id=model_id,
            endpoint_path=endpoint_path,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            headers_received=headers_recvd,
            body_bytes_received=body_recvd,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
            provider_error_code=getattr(diag, "provider_error_code", None) if diag else None,
            provider_error_status=getattr(diag, "provider_error_status", None) if diag else None,
            provider_error_message=getattr(diag, "provider_error_message", None) if diag else None,
            retry_after_seconds=getattr(diag, "retry_after_seconds", None) if diag else None,
            request_id=getattr(diag, "request_id", None) if diag else None,
            trace_id=getattr(diag, "trace_id", None) if diag else None,
            sanitized_headers=getattr(diag, "response_headers_dict", None) if diag else None,
        )
    except Exception as exc:
        elapsed = max(0.0, time.monotonic() - start_time)
        diag = inference.diagnostics[-1] if inference.diagnostics else None
        http_status = getattr(diag, "http_status", None) if diag else None
        headers_recvd = http_status is not None
        body_recvd = bool(
            getattr(diag, "provider_error_status", None)
            or getattr(diag, "provider_error_message", None)
            or getattr(diag, "provider_error_code", None) is not None
        ) if diag else False
        return ExperimentResult(
            experiment_name="1. Minimal Chat Completion Probe",
            model_id=model_id,
            endpoint_path=endpoint_path,
            success=False,
            http_status=http_status,
            elapsed_seconds=elapsed,
            headers_received=headers_recvd,
            body_bytes_received=body_recvd,
            response_text=None,
            interpretation=None,
            diagnostic=diag,
            usage=None,
            exception_type_chain=type(exc).__name__,
            provider_error_code=getattr(diag, "provider_error_code", None) if diag else None,
            provider_error_status=getattr(diag, "provider_error_status", None) if diag else None,
            provider_error_message=getattr(diag, "provider_error_message", None) if diag else None,
            retry_after_seconds=getattr(diag, "retry_after_seconds", None) if diag else None,
            request_id=getattr(diag, "request_id", None) if diag else None,
            trace_id=getattr(diag, "trace_id", None) if diag else None,
            sanitized_headers=getattr(diag, "response_headers_dict", None) if diag else None,
        )


async def run_gemini_structured_experiment(
    inference: AuthenticatedGeminiInference,
    *,
    expected_user_id: str,
    expected_tenant_id: str,
    query: str = "My cotton crop in Warangal has leaf spot",
) -> ExperimentResult:
    """Execute the structured intake experiment returning an ExperimentResult."""
    model_id = inference.model_id or DEFAULT_MODEL_ID
    endpoint_path = "/v1beta/openai/chat/completions"
    success, interp, diag, usage, elapsed = await run_gemini_smoke(
        inference,
        expected_user_id=expected_user_id,
        expected_tenant_id=expected_tenant_id,
        query=query,
    )
    http_status = usage.http_status if (success and usage) else (getattr(diag, "http_status", None) if diag else None)
    headers_recvd = http_status is not None
    if success:
        body_recvd = True
    elif diag is not None:
        body_recvd = bool(
            getattr(diag, "provider_error_status", None)
            or getattr(diag, "provider_error_message", None)
            or getattr(diag, "provider_error_code", None) is not None
        )
    else:
        body_recvd = False

    return ExperimentResult(
        experiment_name="2. Structured Intake Interpretation",
        model_id=model_id,
        endpoint_path=endpoint_path,
        success=success,
        http_status=http_status,
        elapsed_seconds=elapsed,
        headers_received=headers_recvd,
        body_bytes_received=body_recvd,
        response_text=None,
        interpretation=interp,
        diagnostic=diag,
        usage=usage,
        exception_type_chain=getattr(diag, "exception_type_chain", None) if diag else None,
        provider_error_code=getattr(diag, "provider_error_code", None) if diag else None,
        provider_error_status=getattr(diag, "provider_error_status", None) if diag else None,
        provider_error_message=getattr(diag, "provider_error_message", None) if diag else None,
        retry_after_seconds=getattr(diag, "retry_after_seconds", None) if diag else None,
        request_id=(usage.request_id if (success and usage and usage.request_id) else getattr(diag, "request_id", None) if diag else None),
        trace_id=getattr(diag, "trace_id", None) if diag else None,
        sanitized_headers=getattr(diag, "response_headers_dict", None) if diag else None,
    )


def print_experiment_report(result: ExperimentResult) -> None:
    """Print structured, sanitized report for an individual inference experiment."""
    print("-" * 60)
    print(f"Experiment: {result.experiment_name}")
    print("-" * 60)
    print(f"Model ID: {result.model_id}")
    print(f"Endpoint Path: {result.endpoint_path}")
    print(f"Outcome: {'SUCCESS' if result.success else 'FAILURE'}")
    print(f"HTTP Status: {result.http_status if result.http_status is not None else 'none'}")
    print(f"Total Elapsed Time: {result.elapsed_seconds:.3f}s")
    print(f"Response Headers Received: {result.headers_received}")
    print(f"Response Body Bytes Received: {result.body_bytes_received}")
    if result.success:
        if result.response_text:
            print(f"Response Text: {result.response_text.strip()!r}")
        if result.interpretation is not None:
            print("Pydantic Validation: PASSED")
            print(result.interpretation.model_dump_json(indent=2))
        if result.usage:
            print(
                f"Token Usage: prompt={result.usage.actual_input_tokens}, "
                f"completion={result.usage.actual_output_tokens}, "
                f"total={result.usage.actual_total_tokens}"
            )
            if result.usage.request_id:
                print(f"Request ID: {result.usage.request_id}")
    else:
        if result.diagnostic:
            diag = result.diagnostic
            code_str = getattr(diag.code, "value", str(diag.code)) if diag.code else "none"
            stage_str = getattr(diag.stage, "value", str(diag.stage)) if diag.stage else "none"
            print(f"Diagnostic Code: {code_str}")
            print(f"Diagnostic Stage: {stage_str}")
            if diag.exception_type_chain:
                print(f"Exception Type Chain: {diag.exception_type_chain}")
            if getattr(diag, "provider_error_code", None) is not None or diag.provider_error_status or diag.provider_error_message:
                err_parts = []
                if getattr(diag, "provider_error_code", None) is not None:
                    err_parts.append(f"code={diag.provider_error_code}")
                if diag.provider_error_status:
                    err_parts.append(f"status={diag.provider_error_status}")
                if diag.provider_error_message:
                    err_parts.append(f"message={diag.provider_error_message}")
                print(f"Provider Error: {', '.join(err_parts)}")
            if diag.request_id:
                print(f"Request ID: {diag.request_id}")
            if getattr(diag, "trace_id", None):
                print(f"Trace ID: {diag.trace_id}")
            if diag.retry_after_seconds is not None:
                print(f"Retry-After: {diag.retry_after_seconds:.1f}s")
            headers_dict = getattr(diag, "response_headers_dict", {})
            if headers_dict:
                print(f"Sanitized Response Headers: {headers_dict}")
            print(f"Retry Decision: {diag.retry_decision or 'unknown'}")
            print(f"Budget Decision: {diag.budget_decision or 'unknown'}")
        elif result.exception_type_chain:
            print(f"Exception Type Chain: {result.exception_type_chain}")


def print_smoke_report(
    success: bool,
    interpretation: IntakeInterpretation | None,
    diagnostic: InferenceDiagnostic | None,
    usage: InferenceTokenUsage | None,
    elapsed: float,
) -> None:
    """Print structured, sanitized smoke test report."""
    print("=" * 60)
    print("RythuSaathi — Gemini 3.8 Flash Isolated Structured Smoke Test")
    print("=" * 60)
    if success and interpretation is not None:
        http_status = usage.http_status if usage and usage.http_status else 200
        print("Outcome: SUCCESS")
        print(f"HTTP Status: {http_status}")
        print(f"Latency: {elapsed:.3f}s")
        if usage:
            print(f"Finish Reason: {usage.finish_reason or 'unknown'}")
            print(
                f"Token Usage: prompt={usage.actual_input_tokens}, "
                f"completion={usage.actual_output_tokens}, "
                f"total={usage.actual_total_tokens}"
            )
            print(f"Request ID: {usage.request_id or 'none'}")
        print("\nPydantic Structured Validation: PASSED")
        print("Validation Result (JSON):")
        print(interpretation.model_dump_json(indent=2))
    else:
        print("Outcome: FAILURE")
        print(f"Latency: {elapsed:.3f}s")
        if diagnostic is not None:
            code_str = getattr(diagnostic.code, "value", str(diagnostic.code)) if diagnostic.code is not None else "none"
            stage_str = getattr(diagnostic.stage, "value", str(diagnostic.stage)) if diagnostic.stage is not None else "none"
            print(f"HTTP Status: {diagnostic.http_status or 'none'}")
            print(f"Diagnostic Code: {code_str}")
            print(f"Diagnostic Stage: {stage_str}")
            if diagnostic.finish_reason:
                print(f"Finish Reason: {diagnostic.finish_reason}")
            if diagnostic.parsing_outcome:
                print(f"Parsing Outcome: {diagnostic.parsing_outcome}")
            if diagnostic.exception_type_chain:
                print(f"Exception Type Chain: {diagnostic.exception_type_chain}")
            if diagnostic.request_id:
                print(f"Request ID: {diagnostic.request_id}")
            if diagnostic.retry_after_seconds is not None:
                print(f"Retry-After: {diagnostic.retry_after_seconds:.1f}s")
            print(f"Retry Decision: {diagnostic.retry_decision or 'unknown'}")
            print(f"Budget Decision: {diagnostic.budget_decision or 'unknown'}")
            if getattr(diagnostic, "provider_error_code", None) is not None or diagnostic.provider_error_status or diagnostic.provider_error_message:
                err_parts = []
                if getattr(diagnostic, "provider_error_code", None) is not None:
                    err_parts.append(f"code={diagnostic.provider_error_code}")
                if diagnostic.provider_error_status:
                    err_parts.append(f"status={diagnostic.provider_error_status}")
                if diagnostic.provider_error_message:
                    err_parts.append(f"message={diagnostic.provider_error_message}")
                print(f"Provider Error: {', '.join(err_parts)}")
            if getattr(diagnostic, "trace_id", None):
                print(f"Trace ID: {diagnostic.trace_id}")
            headers_dict = getattr(diagnostic, "response_headers_dict", {})
            if headers_dict:
                print(f"Sanitized Response Headers: {headers_dict}")
            transport_codes = {
                InferenceDiagnosticCode.GEMINI_READ_TIMEOUT,
                InferenceDiagnosticCode.GEMINI_CONNECT_TIMEOUT,
                InferenceDiagnosticCode.GEMINI_TIMEOUT,
                InferenceDiagnosticCode.GEMINI_NETWORK_FAILURE,
                "gemini_read_timeout",
                "gemini_connect_timeout",
                "gemini_timeout",
                "gemini_network_failure",
            }
            if diagnostic.code in transport_codes or getattr(diagnostic.code, "value", None) in transport_codes:
                print("\nTransport Diagnostic Tip:")
                print("  Run with --probe-transport to test raw DNS, TCP connect, TLS handshake,")
                print("  and HTTP response-header arrival independently without credentials.")
        else:
            print("HTTP Status: none")
            print("Diagnostic Code: none (no diagnostic recorded)")
    print("=" * 60)


@dataclass(frozen=True)
class TransportProbeResult:
    """Non-inference transport layer diagnostic measurements."""

    host: str
    port: int
    dns_resolved_ips: list[str] = field(default_factory=list)
    dns_ipv4_ips: list[str] = field(default_factory=list)
    dns_latency_seconds: float = 0.0
    dns_error: str | None = None
    tcp_connected_ip: str | None = None
    tcp_latency_seconds: float | None = None
    tcp_error: str | None = None
    tls_negotiated_version: str | None = None
    tls_cipher_suite: str | None = None
    tls_latency_seconds: float | None = None
    tls_error: str | None = None
    http_status_line: str | None = None
    http_headers_latency_seconds: float | None = None
    http_error: str | None = None
    proxy_settings: dict[str, str] = field(default_factory=dict)
    overall_transport_ok: bool = False


def probe_gemini_transport(
    host: str = "generativelanguage.googleapis.com",
    port: int = 443,
    timeout: float = 10.0,
) -> TransportProbeResult:
    """Non-inference transport diagnostic probe.

    Measures DNS resolution, TCP connection establishment, TLS handshake,
    and HTTP response-header arrival for the Google endpoint.
    No credentials, API keys, or prompt payloads are transmitted.
    A successful probe does NOT indicate Gemini inference capability.
    """
    # 0. Safe inspection of proxy environment variables
    proxy_keys = (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    )
    proxies: dict[str, str] = {}
    for k in proxy_keys:
        val = os.environ.get(k)
        if val:
            try:
                parsed = urlparse(val)
                if parsed.password:
                    sanitized = val.replace(f":{parsed.password}@", ":[REDACTED]@")
                else:
                    sanitized = val
            except Exception:
                sanitized = "[SET]"
            proxies[k] = sanitized

    # 1. DNS Resolution
    dns_start = time.monotonic()
    all_ips: list[str] = []
    ipv4_ips: list[str] = []
    dns_err: str | None = None
    try:
        addrinfo = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        seen: set[str] = set()
        for item in addrinfo:
            ip = item[4][0]
            if ip not in seen:
                seen.add(ip)
                all_ips.append(ip)
                if ":" not in ip:
                    ipv4_ips.append(ip)
    except Exception as exc:
        dns_err = f"{type(exc).__name__}: {exc}"

    dns_elapsed = max(0.0, time.monotonic() - dns_start)
    if dns_err or not all_ips:
        return TransportProbeResult(
            host=host,
            port=port,
            dns_resolved_ips=all_ips,
            dns_ipv4_ips=ipv4_ips,
            dns_latency_seconds=dns_elapsed,
            dns_error=dns_err or "No IP addresses resolved",
            proxy_settings=proxies,
            overall_transport_ok=False,
        )

    # 2. TCP Connection
    target_ip = ipv4_ips[0] if ipv4_ips else all_ips[0]
    tcp_start = time.monotonic()
    tcp_err: str | None = None
    sock: socket.socket | None = None
    try:
        family = socket.AF_INET if ":" not in target_ip else socket.AF_INET6
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((target_ip, port))
    except Exception as exc:
        tcp_err = f"{type(exc).__name__}: {exc}"
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
            sock = None

    tcp_elapsed = max(0.0, time.monotonic() - tcp_start)
    if tcp_err or sock is None:
        return TransportProbeResult(
            host=host,
            port=port,
            dns_resolved_ips=all_ips,
            dns_ipv4_ips=ipv4_ips,
            dns_latency_seconds=dns_elapsed,
            tcp_connected_ip=target_ip,
            tcp_latency_seconds=tcp_elapsed,
            tcp_error=tcp_err,
            proxy_settings=proxies,
            overall_transport_ok=False,
        )

    # 3. TLS Handshake
    tls_start = time.monotonic()
    tls_err: str | None = None
    ssock: ssl.SSLSocket | None = None
    tls_version: str | None = None
    cipher_name: str | None = None
    try:
        context = ssl.create_default_context()
        ssock = context.wrap_socket(sock, server_hostname=host)
        tls_version = ssock.version()
        cipher_tuple = ssock.cipher()
        cipher_name = cipher_tuple[0] if cipher_tuple else None
    except Exception as exc:
        tls_err = f"{type(exc).__name__}: {exc}"
        if ssock is not None:
            try:
                ssock.close()
            except Exception:
                pass
            ssock = None
        elif sock is not None:
            try:
                sock.close()
            except Exception:
                pass

    tls_elapsed = max(0.0, time.monotonic() - tls_start)
    if tls_err or ssock is None:
        return TransportProbeResult(
            host=host,
            port=port,
            dns_resolved_ips=all_ips,
            dns_ipv4_ips=ipv4_ips,
            dns_latency_seconds=dns_elapsed,
            tcp_connected_ip=target_ip,
            tcp_latency_seconds=tcp_elapsed,
            tls_latency_seconds=tls_elapsed,
            tls_error=tls_err,
            proxy_settings=proxies,
            overall_transport_ok=False,
        )

    # 4. HTTPS Response-Header Arrival (Unauthenticated minimal HEAD request)
    http_start = time.monotonic()
    http_err: str | None = None
    status_line: str | None = None
    try:
        request_bytes = (
            f"HEAD / HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            f"User-Agent: RythuSaathi-Probe/1.0\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin1")
        ssock.sendall(request_bytes)
        response_data = b""
        while b"\r\n" not in response_data:
            chunk = ssock.recv(1024)
            if not chunk:
                break
            response_data += chunk
        if response_data:
            first_line = response_data.split(b"\r\n", 1)[0].decode("latin1", errors="replace").strip()
            status_line = first_line
        else:
            http_err = "Empty response received from server"
    except Exception as exc:
        http_err = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            ssock.close()
        except Exception:
            pass

    http_elapsed = max(0.0, time.monotonic() - http_start)
    overall_ok = (
        dns_err is None
        and tcp_err is None
        and tls_err is None
        and http_err is None
        and status_line is not None
    )

    return TransportProbeResult(
        host=host,
        port=port,
        dns_resolved_ips=all_ips,
        dns_ipv4_ips=ipv4_ips,
        dns_latency_seconds=dns_elapsed,
        tcp_connected_ip=target_ip,
        tcp_latency_seconds=tcp_elapsed,
        tls_negotiated_version=tls_version,
        tls_cipher_suite=cipher_name,
        tls_latency_seconds=tls_elapsed,
        http_status_line=status_line,
        http_headers_latency_seconds=http_elapsed,
        http_error=http_err,
        proxy_settings=proxies,
        overall_transport_ok=overall_ok,
    )


def print_transport_probe_report(result: TransportProbeResult) -> None:
    """Print structured, non-inference transport probe report."""
    print("=" * 60)
    print("RythuSaathi — Non-Inference Transport Diagnostic Probe")
    print("=" * 60)
    print("WARNING: This probe verifies network and TLS connectivity only.")
    print("A successful transport probe does NOT constitute Gemini LLM inference.")
    print("-" * 60)
    print(f"Target: {result.host}:{result.port}")
    if result.proxy_settings:
        print(f"Environment Proxies: {result.proxy_settings}")
    else:
        print("Environment Proxies: None configured")

    # DNS
    if result.dns_error:
        print(f"DNS Resolution: FAILED ({result.dns_latency_seconds:.3f}s) — {result.dns_error}")
    else:
        print(f"DNS Resolution: PASSED ({result.dns_latency_seconds:.3f}s)")
        print(f"  Resolved IPs: {result.dns_resolved_ips}")
        print(f"  IPv4 IPs: {result.dns_ipv4_ips}")

    # TCP
    if result.tcp_error:
        print(f"TCP Connect: FAILED ({result.tcp_latency_seconds or 0.0:.3f}s) to {result.tcp_connected_ip} — {result.tcp_error}")
    elif result.tcp_connected_ip:
        print(f"TCP Connect: PASSED ({result.tcp_latency_seconds:.3f}s) to {result.tcp_connected_ip}")

    # TLS
    if result.tls_error:
        print(f"TLS Handshake: FAILED ({result.tls_latency_seconds or 0.0:.3f}s) — {result.tls_error}")
    elif result.tls_negotiated_version:
        print(f"TLS Handshake: PASSED ({result.tls_latency_seconds:.3f}s)")
        print(f"  Protocol Version: {result.tls_negotiated_version}")
        print(f"  Cipher Suite: {result.tls_cipher_suite}")

    # HTTP Header
    if result.http_error:
        print(f"HTTP Response Header: FAILED ({result.http_headers_latency_seconds or 0.0:.3f}s) — {result.http_error}")
    elif result.http_status_line:
        print(f"HTTP Response Header: PASSED ({result.http_headers_latency_seconds:.3f}s)")
        print(f"  Status Line: {result.http_status_line}")

    print("-" * 60)
    outcome_str = "PASS" if result.overall_transport_ok else "FAIL"
    print(f"Overall Transport Status: {outcome_str}")
    print("=" * 60)


def setup_authenticated_inference(
    database_path: Path,
    *,
    tenant_id: str | None = None,
    username: str | None = None,
    password: str | None = None,
    credential_id: str | None = None,
    provider: GeminiProvider | None = None,
    key_store: Any | None = None,
    timeout_seconds: float = 15.0,
    max_retries: int = 0,
    model_id: str | None = None,
    thinking_level: str | None = None,
) -> tuple[AuthenticatedGeminiInference, str, str, str, LocalAuthentication]:
    """Authenticate local user and construct AuthenticatedGeminiInference adapter."""
    if not database_path.is_file():
        raise FileNotFoundError(f"Local auth database not found at {database_path}")

    auth = LocalAuthentication(database_path)
    tid = tenant_id or input("Tenant ID: ").strip()
    user = username or input("Username: ").strip()
    pwd = password or getpass.getpass("Password: ")

    session_token = auth.login(user, pwd, tid)
    identity = auth.authenticate(session_token)

    store = key_store or WindowsCredentialStore()
    cred_service = CredentialService(database_path, store)

    cid = credential_id
    if not cid:
        statuses = cred_service.list_status(identity, provider_id="gemini")
        active = [s for s in statuses if s.state.value == "active"]
        if not active:
            auth.logout(session_token)
            raise CredentialUnavailable()
        cid = active[0].credential_id

    if provider is not None:
        prov = provider
    else:
        effective_model = model_id or os.environ.get("GEMINI_MODEL_ID", DEFAULT_MODEL_ID)
        effective_thinking = (
            thinking_level
            if thinking_level is not None
            else os.environ.get("GEMINI_THINKING_LEVEL")
        )
        if effective_thinking and effective_thinking.lower() in {"none", "off", "disabled"}:
            effective_thinking = None
        prov_config = GeminiProviderConfig(
            model_id=effective_model,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=min(10.0, timeout_seconds),
            read_timeout_seconds=timeout_seconds,
            write_timeout_seconds=min(10.0, timeout_seconds),
            pool_timeout_seconds=min(10.0, timeout_seconds),
            max_retries=max_retries,
            thinking_level=effective_thinking,
        )
        prov = GeminiProvider(prov_config)

    service = AuthenticatedGeminiService(auth, cred_service, prov)
    inference = AuthenticatedGeminiInference(service, session_token, cid)
    return inference, identity.subject, identity.tenant_id, session_token, auth


async def async_main(args: argparse.Namespace) -> int:
    auth_db = args.database or (ROOT / "tmp" / "phase5-local-auth.sqlite3")
    try:
        inference, user_id, tenant_id, token, auth = setup_authenticated_inference(
            auth_db,
            tenant_id=args.tenant_id,
            username=args.username,
            password=args.password,
            credential_id=args.credential_id,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
            model_id=args.model_id,
            thinking_level=args.thinking_level,
        )
    except Exception as exc:
        print(f"Setup/Credential lookup failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 2

    try:
        mode = getattr(args, "mode", "both")
        results: list[ExperimentResult] = []

        print("=" * 60)
        print("RythuSaathi — Gemini Live Inference Isolation Experiment")
        print("=" * 60)

        if mode in ("both", "minimal"):
            min_res = await run_gemini_minimal_probe(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
            )
            results.append(min_res)
            print_experiment_report(min_res)

        if mode in ("both", "structured"):
            struct_res = await run_gemini_structured_experiment(
                inference,
                expected_user_id=user_id,
                expected_tenant_id=tenant_id,
                query=args.query,
            )
            results.append(struct_res)
            print_experiment_report(struct_res)

        print("=" * 60)
        return 0 if all(r.success for r in results) else 1
    finally:
        auth.logout(token)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated smoke test for Google Gemini structured inference"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Path to SQLite auth database (defaults to tmp/phase5-local-auth.sqlite3)",
    )
    parser.add_argument("--credential-id", type=str, default=None, help="BYOK credential ID (optional)")
    parser.add_argument("--tenant-id", type=str, default=None, help="Tenant ID (optional)")
    parser.add_argument("--username", type=str, default=None, help="Username (optional)")
    parser.add_argument("--password", type=str, default=None, help="Password (optional)")
    parser.add_argument(
        "--query",
        type=str,
        default="My cotton crop in Warangal has leaf spot",
        help="Farmer query for intake structured test",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Bounded timeout in seconds per attempt (default: 15.0)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=0,
        help="Maximum retries for transient errors (default: 0 for single attempt)",
    )
    parser.add_argument(
        "--model-id",
        type=str,
        default=None,
        help="Gemini model identifier override (defaults to GEMINI_MODEL_ID or gemini-3.8-flash)",
    )
    parser.add_argument(
        "--thinking-level",
        type=str,
        default=None,
        help="Gemini thinking/reasoning effort level (e.g. low, minimal, medium, high, or none)",
    )
    parser.add_argument(
        "--mode",
        choices=["both", "minimal", "structured"],
        default="both",
        help="Experiment mode: both (run minimal then structured), minimal (minimal chat only), or structured (intake interpretation only). Default: both.",
    )
    parser.add_argument(
        "--probe-transport",
        action="store_true",
        help="Run non-inference transport probe (DNS, TCP, TLS, HTTP header arrival) without sending credentials",
    )
    args = parser.parse_args()
    if args.probe_transport:
        base_url = os.environ.get("GEMINI_BASE_URL", GEMINI_BASE_URL)
        parsed_url = urlparse(base_url)
        host = parsed_url.hostname or "generativelanguage.googleapis.com"
        port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
        probe_res = probe_gemini_transport(host=host, port=port, timeout=args.timeout)
        print_transport_probe_report(probe_res)
        return 0 if probe_res.overall_transport_ok else 1
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
