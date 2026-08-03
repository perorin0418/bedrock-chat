#!/usr/bin/env node
import "source-map-support/register";
import * as cdk from "aws-cdk-lib";
import { ClaudeCodeCostSyncStack } from "../lib/claude-code-cost-sync-stack";
import { resolveClaudeCodeCostSyncParameters } from "../lib/utils/parameter-models";

const app = new cdk.App();

const params = resolveClaudeCodeCostSyncParameters(app);
const sepHyphen = params.envPrefix ? "-" : "";

const usageLedgerTableName = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatUsageLedgerTableName`
);
const claudeCodeIamUserTableName = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatClaudeCodeIamUserTableName`
);
const claudeCodeCostSyncRoleArn = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatClaudeCodeCostSyncRoleArn`
);
const claudeCodeNotificationTopicArn = cdk.Fn.importValue(
  `${params.envPrefix}${sepHyphen}BedrockClaudeChatClaudeCodeNotificationTopicArn`
);

// NOTE: DO NOT change the stack id naming rule.
new ClaudeCodeCostSyncStack(app, `ClaudeCodeCostSyncStack${sepHyphen}${params.envPrefix}`, {
  env: {
    region: process.env.CDK_DEFAULT_REGION,
  },
  envPrefix: params.envPrefix,
  usageLedgerTableName,
  claudeCodeIamUserTableName,
  claudeCodeCostSyncRoleArn,
  claudeCodeNotificationTopicArn,
});

cdk.Tags.of(app).add("CDKEnvironment", params.envName);
