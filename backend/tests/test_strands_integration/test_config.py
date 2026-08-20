import os
import sys

os.environ["REGION"] = "us-east-1"
os.environ["BEDROCK_REGION"] = "us-east-1"
os.environ["ENABLE_BEDROCK_GLOBAL_INFERENCE"] = "false"
os.environ["ENABLE_BEDROCK_CROSS_REGION_INFERENCE"] = "true"

sys.path.append(".")

import unittest

from app.repositories.models.custom_bot import (
    GenerationParamsModel,
    ReasoningParamsModel,
)
from app.strands_integration.agent.config import get_bedrock_model_config


class TestGetBedrockModelConfigForGrok(unittest.TestCase):
    """Regression coverage for the real production seam.

    get_bedrock_model_config (not get_model_id or
    generation_params_to_converse_configuration directly) is what the live
    chat path actually calls to build the Strands BedrockModel.BedrockConfig.
    A regression here would leak temperature/top_p/prompt caching into a
    live Grok call even if the lower-level helpers stayed correct in
    isolation, so this needs its own test.
    """

    def _generation_params(self) -> GenerationParamsModel:
        return GenerationParamsModel(
            max_tokens=2000,
            top_k=250,
            top_p=0.9,
            temperature=0.7,
            stop_sequences=["Human: ", "Assistant: "],
            reasoning_params=ReasoningParamsModel(budget_tokens=1024),
        )

    def test_grok_config_excludes_unsupported_sampling_and_caching_fields(self):
        config = get_bedrock_model_config(
            model_name="grok-4.6",
            instructions=["You are a helpful assistant."],
            generation_params=self._generation_params(),
            prompt_caching_enabled=True,
            enable_reasoning=False,
            has_tools=True,
        )

        self.assertIn("max_tokens", config)

        self.assertNotIn("temperature", config)
        self.assertNotIn("top_p", config)
        self.assertNotIn("stop_sequences", config)

        # Grok rejects cachePoint outright (AccessDeniedException on the live
        # API), so prompt caching must never be enabled for it even though
        # prompt_caching_enabled=True and has_tools=True are passed in.
        self.assertNotIn("cache_prompt", config)
        self.assertNotIn("cache_tools", config)

        self.assertEqual(
            config["additional_request_fields"],
            {"reasoning": {"effort": "low"}},
        )


if __name__ == "__main__":
    unittest.main()
