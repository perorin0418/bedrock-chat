import os
import sys

import boto3
from ulid import ULID

os.environ["REGION"] = "us-west-2"
os.environ["BEDROCK_REGION"] = "us-west-2"
os.environ["ENABLE_BEDROCK_GLOBAL_INFERENCE"] = "true"
os.environ["ENABLE_BEDROCK_CROSS_REGION_INFERENCE"] = "true"

sys.path.append(".")

import unittest
from pprint import pprint
from unittest.mock import patch

from app.bedrock import (
    call_converse_api,
    compose_args_for_converse_api,
    generation_params_to_converse_configuration,
    get_model_id,
    is_prompt_caching_supported,
    is_temperature_supported,
    is_top_k_supported,
    is_top_p_supported,
)
from app.repositories.models.conversation import SimpleMessageModel, TextContentModel
from app.repositories.models.custom_bot import (
    GenerationParamsModel,
    ReasoningParamsModel,
)
from app.repositories.models.custom_bot_guardrails import BedrockGuardrailsModel
from app.routes.schemas.conversation import type_model_name

# MODEL: type_model_name = "claude-v3-haiku"
MODEL: type_model_name = "claude-v3.7-sonnet"


class TestGetModelId(unittest.TestCase):
    def test_get_model_id_with_cross_region_supported_model(self):
        model = "claude-v3.5-sonnet"
        # Prefix with "us." to enable cross-region
        expected_model_id = "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="us-east-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_without_cross_region(self):
        model = "claude-v3.5-sonnet"
        # No prefix to disable cross-region
        expected_model_id = "anthropic.claude-3-5-sonnet-20240620-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=False,
                enable_cross_region=False,
                bedrock_region="us-east-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_with_unsupported_region_for_cross_region(self):
        model = "claude-v3.5-sonnet"
        # Cross region is disabled because the region is not supported
        expected_model_id = "apac.anthropic.claude-3-5-sonnet-20240620-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="ap-northeast-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_with_global_inference_priority(self):
        """Global inference is selected for supported model and region"""
        model = "claude-v4-sonnet"
        expected_model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="us-east-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_without_global_with_cross_region_inference_priority(self):
        """Global inference is selected for supported model and region"""
        model = "claude-v4-sonnet"
        expected_model_id = "us.anthropic.claude-sonnet-4-20250514-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=False,
                enable_cross_region=True,
                bedrock_region="us-east-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_with_regional_fallback(self):
        """Falls back to regional cross-region for non-global supported models"""
        model = "claude-v3.5-sonnet"  # Non-global supported
        expected_model_id = "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="us-east-1",
            ),
            expected_model_id,
        )

    def test_get_model_id_with_unsupported_region_fallback(self):
        """Falls back to regional for global-supported model in unsupported region"""
        model = "claude-v4-sonnet"
        # ap-south-1 is not global-supported but APAC regional-supported
        expected_model_id = "apac.anthropic.claude-sonnet-4-20250514-v1:0"
        self.assertEqual(
            get_model_id(
                model,
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="ap-south-1",
            ),
            expected_model_id,
        )


class TestGrokModelId(unittest.TestCase):
    def test_grok_uses_global_profile(self):
        self.assertEqual(
            get_model_id(
                "grok-4.6",
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="ap-northeast-1",
            ),
            "global.xai.grok-4.6",
        )

    def test_grok_ignores_geo_profile_and_stays_global(self):
        # us-west-2 supports a geo (us.) profile for grok-4.6 too — AWS
        # confirms `us.xai.grok-4.6` is ACTIVE there — but Grok deliberately
        # never uses it, even when cross-region is enabled and a geo profile
        # would otherwise be picked. PROFILE_REQUIRED_MODELS forces the global
        # profile unconditionally so that billing (calculate_price, which
        # keys off deployment region, not the resolved profile) always maps
        # to the single `default`/global BEDROCK_PRICING entry. If this
        # assertion ever starts failing because someone "fixed" the ordering
        # back, that is the regression this test exists to catch.
        self.assertEqual(
            get_model_id(
                "grok-4.6",
                enable_global=False,
                enable_cross_region=True,
                bedrock_region="us-west-2",
            ),
            "global.xai.grok-4.6",
        )

    def test_grok_forces_global_profile_when_both_flags_disabled(self):
        # Grok cannot be invoked with on-demand throughput, so the bare model
        # ID must never be returned even when both flags are off.
        self.assertEqual(
            get_model_id(
                "grok-4.6",
                enable_global=False,
                enable_cross_region=False,
                bedrock_region="ap-northeast-1",
            ),
            "global.xai.grok-4.6",
        )

    def test_grok_raises_on_region_without_global_profile(self):
        # Match the message so this test cannot pass on the unrelated
        # "Unsupported model" ValueError raised before the model is registered.
        with self.assertRaisesRegex(ValueError, "requires an inference profile"):
            get_model_id(
                "grok-4.6",
                enable_global=True,
                enable_cross_region=True,
                bedrock_region="us-gov-west-1",
            )


class TestGrokConverseConfiguration(unittest.TestCase):
    def _generation_params(self) -> GenerationParamsModel:
        return GenerationParamsModel(
            max_tokens=2000,
            top_k=250,
            top_p=0.9,
            temperature=0.7,
            stop_sequences=["Human: ", "Assistant: "],
            reasoning_params=ReasoningParamsModel(budget_tokens=1024),
        )

    def test_grok_config_omits_unsupported_inference_params(self):
        # Grok rejects temperature, topP and stopSequences outright.
        config = generation_params_to_converse_configuration(
            model="grok-4.6",
            generation_params=self._generation_params(),
        )
        self.assertEqual(config["inferenceConfig"], {"maxTokens": 2000})

    def test_grok_config_sets_reasoning_effort(self):
        config = generation_params_to_converse_configuration(
            model="grok-4.6",
            generation_params=self._generation_params(),
        )
        self.assertEqual(
            config["additionalModelRequestFields"],
            {"reasoning": {"effort": "low"}},
        )

    def test_grok_config_ignores_enable_reasoning_flag(self):
        # Reasoning is always on for Grok, so the flag must not change the shape.
        enabled = generation_params_to_converse_configuration(
            model="grok-4.6",
            generation_params=self._generation_params(),
            enable_reasoning=True,
        )
        disabled = generation_params_to_converse_configuration(
            model="grok-4.6",
            generation_params=self._generation_params(),
            enable_reasoning=False,
        )
        self.assertEqual(enabled, disabled)

    def test_grok_sampling_params_reported_unsupported(self):
        self.assertFalse(is_temperature_supported("grok-4.6"))
        self.assertFalse(is_top_p_supported("grok-4.6"))
        self.assertFalse(is_top_k_supported("grok-4.6"))

    def test_grok_prompt_caching_reported_unsupported(self):
        # Verified against the live API: cachePoint returns AccessDeniedException.
        self.assertFalse(is_prompt_caching_supported("grok-4.6", target="system"))
        self.assertFalse(is_prompt_caching_supported("grok-4.6", target="message"))
        self.assertFalse(is_prompt_caching_supported("grok-4.6", target="tool"))


class TestCallConverseApi(unittest.TestCase):
    def test_call_converse_api_with_global_inference(self):
        """Actual LLM call using global inference profile"""
        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello! Please respond with just 'Global inference works!'",
                )
            ],
        )

        # Use global inference supported model
        arg = compose_args_for_converse_api(
            [message],
            "claude-v4-sonnet",  # Global inference supported
            stream=False,
        )

        # Verify modelId is global profile
        expected_model_id = "global.anthropic.claude-sonnet-4-20250514-v1:0"
        self.assertEqual(arg["modelId"], expected_model_id)

        # Actual API call
        response = call_converse_api(arg)

        # Verify basic response structure
        self.assertIn("output", response)
        self.assertIn("message", response["output"])
        self.assertIn("content", response["output"]["message"])

        print(f"Global inference response: {response['output']['message']['content']}")

    def test_call_converse_api_with_regional_fallback(self):
        """Actual LLM call with regional cross-region fallback"""
        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello! Please respond with just 'Regional inference works!'",
                )
            ],
        )

        # Use non-global supported model
        arg = compose_args_for_converse_api(
            [message],
            "claude-v3.5-sonnet",  # Non-global supported, regional supported
            stream=False,
        )

        # Verify modelId is regional profile
        expected_model_id = "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
        self.assertEqual(arg["modelId"], expected_model_id)

        # Actual API call
        response = call_converse_api(arg)

        # Verify basic response structure
        self.assertIn("output", response)
        self.assertIn("message", response["output"])
        self.assertIn("content", response["output"]["message"])

        print(
            f"Regional inference response: {response['output']['message']['content']}"
        )

    def test_call_converse_api(self):
        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello, World!",
                )
            ],
        )
        arg = compose_args_for_converse_api(
            [message],
            MODEL,
            stream=False,
        )

        response = call_converse_api(arg)
        pprint(response)


class TestCallConverseApiWithGuardrails(unittest.TestCase):
    def setUp(self):
        # Note that the region must be the same as the one used in the bedrock client
        # https://github.com/aws/aws-sdk-js-v3/issues/6482
        self.bedrock_client = boto3.client("bedrock", region_name="us-east-1")
        self.guardrail_name = f"test-guardrail-{ULID()}"

        # Create dummy guardrail
        res = self.bedrock_client.create_guardrail(
            name=self.guardrail_name,
            description="Test guardrail for unit tests",
            contentPolicyConfig={
                "filtersConfig": [
                    {"type": "SEXUAL", "inputStrength": "LOW", "outputStrength": "LOW"},
                ]
            },
            blockedInputMessaging="blocked",
            blockedOutputsMessaging="blocked",
        )

        res_ver = self.bedrock_client.create_guardrail_version(
            guardrailIdentifier=res["guardrailArn"],
        )

        self.guardrail = BedrockGuardrailsModel(
            is_guardrail_enabled=True,
            hate_threshold=0,
            insults_threshold=0,
            sexual_threshold=1,
            violence_threshold=0,
            misconduct_threshold=0,
            grounding_threshold=0,
            relevance_threshold=0,
            guardrail_arn=res["guardrailArn"],
            guardrail_version=res_ver["version"],
            # guardrail_version="DRAFT",
        )
        self.guardrail_arn = res["guardrailArn"]

    def tearDown(self):
        print("Cleaning up...")
        # Delete dummy guardrail
        try:
            self.bedrock_client.delete_guardrail(guardrailIdentifier=self.guardrail_arn)

        except Exception as e:
            print(f"Error deleting guardrail: {e}")

    def test_call_converse_api_with_guardrails(self):
        message = SimpleMessageModel(
            role="user",
            content=[
                TextContentModel(
                    content_type="text",
                    body="Hello, World!",
                )
            ],
        )
        arg = compose_args_for_converse_api(
            [message],
            MODEL,
            guardrail=self.guardrail,
            stream=False,
        )

        pprint(arg)

        response = call_converse_api(arg)
        pprint(response)


if __name__ == "__main__":
    unittest.main()
