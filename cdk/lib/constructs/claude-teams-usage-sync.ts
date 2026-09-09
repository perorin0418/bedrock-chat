import { BundlingFileAccess, Duration, Stack } from "aws-cdk-lib";
import * as python from "@aws-cdk/aws-lambda-python-alpha";
import { Runtime } from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as logs from "aws-cdk-lib/aws-logs";
import { Construct } from "constructs";
import * as path from "path";
import { Database } from "./database";

export interface ClaudeTeamsUsageSyncProps {
  readonly database: Database;
}

/**
 * Hourly Lambda that samples every registered Claude Teams OAuth token's
 * 5-hour/7-day usage-limit consumption and token validity (Anthropic's
 * `/api/oauth/usage` endpoint) and appends one snapshot per token to
 * `ClaudeTeamsUsageHistoryTable`. These are two independent signals: usage
 * consumption resets automatically over time, while token validity
 * ("期限切れ") only changes when the token is revoked/expired outright
 * (see backend/claude_teams_usage_sync/index.py). The admin screen reads
 * the latest snapshot per token to show current status; the CSV export
 * reads a date range.
 *
 * Kept as a Construct inside the main stack (unlike ClaudeCodeCostSyncStack,
 * which is deliberately a separate CDK app because it holds
 * iam:PutUserPolicy/DeleteUserPolicy) since this Lambda's blast radius is
 * limited to Secrets Manager read (token strings) and DynamoDB read/write
 * on its own two tables.
 */
export class ClaudeTeamsUsageSync extends Construct {
  constructor(scope: Construct, id: string, props: ClaudeTeamsUsageSyncProps) {
    super(scope, id);

    const { database } = props;

    const handlerRole = new iam.Role(this, "HandlerRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    handlerRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        "service-role/AWSLambdaBasicExecutionRole"
      )
    );
    handlerRole.addToPolicy(
      // Assume the main stack's dedicated role for token-table reads and
      // usage-history writes (see database.ts's ClaudeTeamsUsageSyncRole).
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [database.claudeTeamsUsageSyncRole.roleArn],
      })
    );
    handlerRole.addToPolicy(
      // The OAuth token strings themselves live in Secrets Manager, not
      // behind the assumed role (same secret prefix api.ts's handlerRole
      // is granted read/write on).
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:claude-teams-token/*`,
        ],
      })
    );

    const handler = new python.PythonFunction(this, "Handler", {
      entry: path.join(__dirname, "../../../backend/claude_teams_usage_sync/"),
      runtime: Runtime.PYTHON_3_13,
      timeout: Duration.minutes(5),
      role: handlerRole,
      environment: {
        CLAUDE_TEAMS_USAGE_SYNC_ROLE_ARN: database.claudeTeamsUsageSyncRole.roleArn,
        CLAUDE_TEAMS_TOKEN_TABLE_NAME: database.claudeTeamsTokenTable.tableName,
        CLAUDE_TEAMS_USAGE_HISTORY_TABLE_NAME:
          database.claudeTeamsUsageHistoryTable.tableName,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
      bundling: {
        bundlingFileAccess: BundlingFileAccess.VOLUME_COPY,
      },
    });

    new events.Rule(this, "ScheduleRule", {
      schedule: events.Schedule.rate(Duration.hours(1)),
      targets: [new targets.LambdaFunction(handler)],
    });
  }
}
