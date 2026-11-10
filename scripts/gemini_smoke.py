"""Isolated smoke test for Google Gemini 3.8 Flash endpoint.

Tests:
1. Raw TCP & TLS connect to generativelanguage.googleapis.com:443
2. GET /models via OpenAI-compatible endpoint
3. Minimal chat completion
4. Minimal chat completion forced to IPv4
5. Chat completion with reasoning_effort="low"
6. Chat completion with strict json_schema + reasoning_effort="low"
"""

from __future__ import annotations

import asyncio
import getpass
import socket
import ssl
import sys
import time

import httpx
from openai import AsyncOpenAI, Timeout

HOST = "generativelanguage.googleapis.com"
PORT = 443
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL_ID = "gemini-3.8-flash"


async def test_raw_tcp() -> None:
    print("\n--- Test 1: Raw TCP & TLS Connect ---")
    start = time.monotonic()
    try:
        # Check DNS resolution
        addr_info = socket.getaddrinfo(HOST, PORT, proto=socket.IPPROTO_TCP)
        ips = list(dict.fromkeys(info[4][0] for info in addr_info))
        print(f"DNS resolved {HOST} -> {ips}")

        # Connect
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT, ssl=True),
            timeout=10.0,
        )
        elapsed = time.monotonic() - start
        peer = writer.get_extra_info("peername")
        cipher = writer.get_extra_info("cipher")
        print(f"PASS: Connected to {peer} in {elapsed:.3f}s (Cipher: {cipher[0] if cipher else 'unknown'})")
        writer.close()
        await writer.wait_closed()
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: Raw TCP/TLS connect failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")


async def test_get_models(api_key: str) -> None:
    print("\n--- Test 2: GET /models ---")
    start = time.monotonic()
    client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL, timeout=Timeout(timeout=15.0, connect=10.0))
    try:
        models_page = await client.models.list()
        elapsed = time.monotonic() - start
        model_ids = [m.id for m in models_page.data]
        has_target = MODEL_ID in model_ids
        print(f"PASS: HTTP 200 in {elapsed:.3f}s. Total models: {len(model_ids)}. Contains {MODEL_ID}: {has_target}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: GET /models failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")
    finally:
        await client.close()


async def test_minimal_chat(api_key: str) -> None:
    print("\n--- Test 3: Minimal Chat (no thinking, no schema) ---")
    start = time.monotonic()
    client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL, timeout=Timeout(timeout=30.0, connect=10.0))
    try:
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": "Reply with the single word 'HELLO' and nothing else."}],
            max_tokens=50,
            temperature=0,
        )
        elapsed = time.monotonic() - start
        choice = resp.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason
        usage = resp.usage
        print(f"PASS: Chat completed in {elapsed:.3f}s | finish_reason={finish_reason}")
        print(f"      Usage: prompt={usage.prompt_tokens if usage else '?'}, completion={usage.completion_tokens if usage else '?'}")
        print(f"      Content: {content.strip()!r}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: Minimal chat failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")
    finally:
        await client.close()


async def test_minimal_chat_ipv4(api_key: str) -> None:
    print("\n--- Test 4: Minimal Chat Forced IPv4 (0.0.0.0 local address) ---")
    start = time.monotonic()
    http_transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0")
    custom_http = httpx.AsyncClient(transport=http_transport, timeout=30.0)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=BASE_URL,
        http_client=custom_http,
        max_retries=0,
    )
    try:
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": "Reply with the single word 'IPV4' and nothing else."}],
            max_tokens=50,
            temperature=0,
        )
        elapsed = time.monotonic() - start
        choice = resp.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason
        usage = resp.usage
        print(f"PASS: IPv4 chat completed in {elapsed:.3f}s | finish_reason={finish_reason}")
        print(f"      Usage: prompt={usage.prompt_tokens if usage else '?'}, completion={usage.completion_tokens if usage else '?'}")
        print(f"      Content: {content.strip()!r}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: IPv4 chat failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")
    finally:
        await client.close()


async def test_chat_reasoning_effort(api_key: str) -> None:
    print("\n--- Test 5: Chat with reasoning_effort='low' ---")
    start = time.monotonic()
    client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL, timeout=Timeout(timeout=30.0, connect=10.0))
    try:
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": "Reply with the single word 'REASONING' and nothing else."}],
            max_tokens=200,
            temperature=0,
            reasoning_effort="low",
        )
        elapsed = time.monotonic() - start
        choice = resp.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason
        usage = resp.usage
        print(f"PASS: Completed in {elapsed:.3f}s | finish_reason={finish_reason}")
        print(f"      Usage: prompt={usage.prompt_tokens if usage else '?'}, completion={usage.completion_tokens if usage else '?'}")
        print(f"      Content: {content.strip()!r}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: reasoning_effort='low' failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")
    finally:
        await client.close()


async def test_chat_strict_schema_reasoning(api_key: str) -> None:
    print("\n--- Test 6: Chat with strict json_schema + reasoning_effort='low' ---")
    start = time.monotonic()
    client = AsyncOpenAI(api_key=api_key, base_url=BASE_URL, timeout=Timeout(timeout=30.0, connect=10.0))
    schema = {
        "name": "greeting_schema",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "greeting": {"type": "string"},
                "status": {"type": "string"},
            },
            "required": ["greeting", "status"],
            "additionalProperties": False,
        },
    }
    try:
        resp = await client.chat.completions.create(
            model=MODEL_ID,
            messages=[{"role": "user", "content": "Return greeting='Hello' and status='OK' as JSON."}],
            max_tokens=256,
            temperature=0,
            reasoning_effort="low",
            response_format={"type": "json_schema", "json_schema": schema},
        )
        elapsed = time.monotonic() - start
        choice = resp.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason
        usage = resp.usage
        print(f"PASS: Completed in {elapsed:.3f}s | finish_reason={finish_reason}")
        print(f"      Usage: prompt={usage.prompt_tokens if usage else '?'}, completion={usage.completion_tokens if usage else '?'}")
        print(f"      Content: {content.strip()!r}")
    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"FAIL: strict json_schema + reasoning failed after {elapsed:.3f}s ({type(exc).__name__}: {exc})")
    finally:
        await client.close()


async def main() -> int:
    print("=" * 60)
    print("RythuSaathi — Gemini 3.8 Flash Isolated Live Smoke Test")
    print("=" * 60)

    try:
        key = getpass.getpass("Enter Gemini API key (input is hidden): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1

    if not key:
        print("Error: API key cannot be empty.")
        return 1

    print("\nStarting tests...")
    await test_raw_tcp()
    await test_get_models(key)
    await test_minimal_chat(key)
    await test_minimal_chat_ipv4(key)
    await test_chat_reasoning_effort(key)
    await test_chat_strict_schema_reasoning(key)

    key = ""  # scrub memory
    print("\n" + "=" * 60)
    print("Smoke test finished.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

