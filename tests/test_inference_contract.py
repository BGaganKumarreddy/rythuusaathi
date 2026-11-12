from __future__ import annotations

import unittest
from typing import get_type_hints

from agent_framework import ChatOptions, ChatResponse, Content, Message
from typing_extensions import is_typeddict
from pydantic import BaseModel, Field

from rythusaathi.inference.contracts import (
    InferenceProvider,
    InferenceRequest,
    ProviderCredential,
    ProviderFailure,
    ProviderResponse,
    sanitized_chat_response,
)


class ContractOnlyProvider:
    """A provider-neutral test double; deliberately imports no adapter SDK."""

    async def complete(self, request, credential):
        del credential
        return ProviderResponse("fake", "fixture-model", ChatResponse(messages=list(request.messages)))


class StructuredResult(BaseModel):
    summary: str
    details: dict[str, list[str]] = Field(default_factory=dict)


class InferenceContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_options_follow_framework_typed_dict_contract(self):
        self.assertTrue(is_typeddict(ChatOptions))
        self.assertEqual(get_type_hints(InferenceRequest)["options"], ChatOptions | None)

    async def test_provider_protocol_is_usable_without_concrete_adapter(self):
        provider = ContractOnlyProvider()
        self.assertIsInstance(provider, InferenceProvider)
        result = await provider.complete(
            InferenceRequest([Message("user", ["fixture"])]),
            ProviderCredential("fixture-secret"),
        )
        self.assertEqual(result.provider, "fake")
        self.assertEqual(result.response.messages[0].contents[0].text, "fixture")

    async def test_public_framework_serializers_preserve_response_fields(self):
        original = ChatResponse(
            messages=[Message("assistant", [Content(type="text", text="answer")])],
            response_id="response-fixture",
            conversation_id="conversation-fixture",
            model="fixture-model",
            finish_reason="stop",
            usage_details={"input_token_count": 3, "output_token_count": 2},
            additional_properties={"safe_metadata": {"source": "fixture"}},
            value=StructuredResult(summary="structured answer"),
        )
        sanitized = sanitized_chat_response(original, "not-present")
        self.assertEqual(sanitized.response_id, original.response_id)
        self.assertEqual(sanitized.conversation_id, original.conversation_id)
        self.assertEqual(sanitized.model, original.model)
        self.assertEqual(sanitized.finish_reason, original.finish_reason)
        self.assertEqual(sanitized.usage_details, original.usage_details)
        self.assertEqual(sanitized.additional_properties, original.additional_properties)
        self.assertEqual(sanitized.value, original.value)
        self.assertEqual(sanitized.messages[0].contents[0].text, "answer")

    async def test_nested_tool_arguments_and_metadata_are_scrubbed(self):
        canary = "fixture-secret-canary"
        content = Content(
            type="function_call",
            call_id="call-fixture",
            name="fixture_tool",
            arguments={"nested": [canary]},
            additional_properties={"trace": {"description": canary}},
        )
        safe = sanitized_chat_response(ChatResponse(messages=[Message("assistant", [content])]), canary)
        serialized = safe.to_dict(exclude_none=False)
        self.assertNotIn(canary, str(serialized))
        self.assertEqual(safe.messages[0].contents[0].arguments, {"nested": ["[REDACTED]"]})

    async def test_nested_structured_output_is_recursively_sanitized(self):
        canary = "structured-output-secret"
        value = StructuredResult(
            summary="safe summary",
            details={"evidence": ["safe evidence", canary]},
        )
        safe = sanitized_chat_response(
            ChatResponse(messages=[Message("assistant", ["answer"])], value=value), canary
        )
        self.assertIsInstance(safe.value, StructuredResult)
        self.assertEqual(safe.value.details, {"evidence": ["safe evidence", "[REDACTED]"]})

    async def test_unsupported_nested_values_fail_closed(self):
        class Opaque:
            pass

        cases = [
            ChatResponse(messages=[Message("assistant", [Content(type="function_call", arguments={"x": Opaque()})])]),
            ChatResponse(messages=[Message("assistant", ["safe"], additional_properties={"x": Opaque()})]),
        ]
        for response in cases:
            with self.subTest(response_type=type(response).__name__):
                with self.assertRaises(ProviderFailure):
                    sanitized_chat_response(response, "fixture-secret")

    async def test_sanitization_when_lazy_response_value_parsing_raises(self):
        # When response_format is set and assistant message contains text that
        # cannot be decoded by the framework's default JSON parser (e.g. markdown code blocks),
        # sanitized_chat_response must not fail-closed on lazy .value property evaluation;
        # it must preserve the original assistant text and response metadata.
        fenced_text = "```json\n{\"summary\": \"test\"}\n```"
        response = ChatResponse(
            messages=[Message("assistant", [fenced_text])],
            response_id="resp-123",
            response_format={"type": "json_object"},
            usage_details={"total_token_count": 42},
        )
        safe = sanitized_chat_response(response, "fixture-secret")
        self.assertEqual(safe.response_id, "resp-123")
        self.assertEqual(safe.usage_details, {"total_token_count": 42})
        self.assertEqual(safe.messages[0].contents[0].text, fenced_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
