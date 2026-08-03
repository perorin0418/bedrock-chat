# Claude Code Cost Sync

## Overview

This sample includes an opt-in feature that lets employees use the Claude Code CLI directly against Amazon Bedrock (via a personal IAM user), while keeping their combined spend (Bedrock Chat usage + direct Claude Code usage) under the same per-user rate limits Bedrock Chat already enforces (5-hour / 7-day rolling USD limits).

Because Claude Code talks to Bedrock directly and never goes through the Bedrock Chat backend, it can't be rate-limited in real time the way in-app chat is. Instead, this feature:

1. Provisions a dedicated IAM user (`claude-code-{user_id}`) and Bedrock access key for each employee at Cognito sign-up.
2. Collects that IAM user's Bedrock cost via AWS Cost and Usage Report (CUR) 2.0 + Athena, on an hourly schedule.
3. Merges that cost into the same DynamoDB usage ledger Bedrock Chat uses for its own rate limiting.
4. Automatically attaches (and later removes) an IAM Deny policy on the employee's IAM user when they go over the limit.

**Known trade-off**: CUR data has up to ~24 hours of delivery lag, and the sync runs hourly, so Claude Code usage is reflected in the combined limit with some delay (unlike in-app chat, which is enforced in real time). The 7-day window absorbs this well; the 5-hour window is only approximate for the Claude Code portion.

**Out of scope (not implemented)**: offboarding automation (deleting the IAM user/access key when an employee's Cognito account is removed). There is currently no Cognito user-deletion hook in this codebase to attach this to; it would need to be built as a separate feature.

## Architecture

```
[Cognito PostConfirmation_ConfirmSignUp]
  -> add_user_to_groups Lambda (existing trigger, extended)
       -> creates IAM user "claude-code-{sub}" + attaches ClaudeCodeBedrockAccessPolicy
       -> creates an access key, stores it in Secrets Manager ("claude-code/{sub}")
       -> records provisioning state in ClaudeCodeIamUserTable

[CUR 2.0 Data Export] (same AWS account as Bedrock Chat, created by CDK)
  -> Glue Crawler (daily) catalogs the Parquet export
  -> Athena queries it

[EventBridge rule, hourly]
  -> claude_code_cost_sync Lambda
       -> assumes ClaudeCodeCostSyncRole (main stack)
       -> queries Athena for each claude-code-* IAM principal's daily cost (last 8 days)
       -> writes it into the same DynamoDB usage ledger Bedrock Chat's own rate limiter reads
       -> re-evaluates each employee's 5h/7d totals and attaches/removes an IAM Deny policy
       -> publishes an SNS notification on sync failures and on newly-applied Deny
```

CUR 2.0 supports per-IAM-principal cost allocation (`line_item_iam_principal`) natively — no AWS Organizations management account involvement is needed, since a member account's own CUR export already contains only that account's own usage, and Claude Code's IAM users live in this same account.

## Deployment

This feature is split across **two independent CDK apps**, the same pattern this repo already uses for `ApiPublishmentStack`:

- The main app (`bin/bedrock-chat.ts`, the default `cdk.json` `"app"`) owns the IAM-provisioning side: `ClaudeCodeIamUserTable`, `ClaudeCodeCostSyncRole`, `ClaudeCodeBedrockAccessPolicy`, the Cognito trigger changes, and the notification SNS topic.
- A separate app (`bin/claude-code-cost-sync.ts`) owns the CUR 2.0 export, Athena/Glue setup, and the hourly sync Lambda. It's kept separate because it holds `iam:PutUserPolicy`/`iam:DeleteUserPolicy` permissions — a stronger blast radius than most of the rest of the app — and because it has cross-stack CloudFormation references (`Fn.importValue`) onto exports produced by the main stack.

### 1. Enable/configure the feature

`enableClaudeCodeProvisioning` defaults to `true` in `cdk/cdk.json`. Set it to `false` there (or in `parameter.ts`, if you've defined a `"default"` environment there — in which case `cdk.json` context is ignored entirely) if you don't want every Cognito sign-up to provision an IAM user and access key.

Optionally set `claudeCodeNotificationEmail` to an email address to subscribe it to the notification SNS topic (IAM provisioning failures, cost-sync failures, and newly-applied Deny events).

### 2. Deploy the main stack (as usual)

```bash
cd cdk
npx cdk deploy --all
```

### 3. Deploy the cost-sync app (separately, after step 2)

The cost-sync app imports CloudFormation exports from the main stack, so it must be deployed **after** it:

```bash
npx cdk deploy --all --app "npx ts-node --prefer-ts-exts bin/claude-code-cost-sync.ts"
```

`npx cdk deploy --all` on its own (without the `--app` override) only deploys the stacks defined in `bin/bedrock-chat.ts` — it does **not** pick up this second app, the same way it doesn't deploy `ApiPublishmentStack`.

### Notes / caveats

- The `bcm-data-exports` service (CUR 2.0 Data Exports) only has a **us-east-1** API endpoint, and `AWS::BCMDataExports::Export` is likewise only registered as a CloudFormation resource type in us-east-1. Deploying `ClaudeCodeCostSyncStack` to any other region works anyway: the CUR export itself is created by a small Lambda-backed custom resource (`cdk/custom-resources/claude-code-cur-export/`) that explicitly targets `region_name="us-east-1"` in its own boto3 client, independent of which region the Lambda (and the rest of this stack: the S3 bucket, Glue, Athena, the sync Lambda) actually runs in. No special deploy-role permissions are needed for this — the custom resource Lambda has its own `bcm-data-exports:*` permissions scoped in the stack.
- After first deploy, CUR 2.0 data can take up to ~24 hours to be delivered, and the Glue Crawler that catalogs it runs once daily — so the sync Lambda will see "no CUR table yet" errors (logged, not alarming) until both have run at least once.
- IAM access keys are long-lived credentials. This sample stores them in Secrets Manager and expects an administrator to retrieve and hand them off to employees through your organization's existing secure-sharing channel — it does not implement self-service retrieval or key rotation.

## Security considerations

- The employee's IAM user is scoped to a customer-managed policy (`ClaudeCodeBedrockAccessPolicy`) allowing only `bedrock:InvokeModel*`/`Converse*` actions.
- The Deny policy attached when over budget (`ClaudeCodeRateLimitDeny`) only denies those same Bedrock actions — it does not touch any other permission the IAM user might have.
- All IAM/Secrets Manager permissions granted to the Lambdas in this feature are scoped by resource-ARN pattern to `user/claude-code-*` / `secret:claude-code/*`, so they cannot affect any other IAM identity or secret in the account.
