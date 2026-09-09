import { CfnOutput, BundlingFileAccess, Duration, Stack, StackProps } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as events from "aws-cdk-lib/aws-events";
import * as targets from "aws-cdk-lib/aws-events-targets";
import * as python from "@aws-cdk/aws-lambda-python-alpha";
import * as sns from "aws-cdk-lib/aws-sns";
import { Runtime } from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as path from "path";
import { rateLimitParamName } from "./utils/parameter-models";
import { ClaudeCodeCurAnalysis } from "./constructs/claude-code-cur-analysis";

export interface ClaudeCodeCostSyncStackProps extends StackProps {
  readonly envPrefix: string;
  readonly usageLedgerTableName: string;
  readonly claudeCodeIamUserTableName: string;
  readonly claudeCodeCostSyncRoleArn: string;
  readonly claudeCodeNotificationTopicArn: string;
}

/**
 * Independent CDK app (same pattern as ApiPublishmentStack) that owns the
 * CUR 2.0 export/Athena setup and the hourly Lambda syncing Claude Code
 * (direct Bedrock access) costs into the main stack's usage ledger, plus
 * Deny-policy enforcement for employees over their rate limit.
 *
 * Kept as its own stack (rather than folded into the main stack) because it
 * holds iam:PutUserPolicy/DeleteUserPolicy permissions -- a stronger blast
 * radius than most of the main app -- and has its own deploy lifecycle.
 */
export class ClaudeCodeCostSyncStack extends Stack {
  constructor(
    scope: Construct,
    id: string,
    props: ClaudeCodeCostSyncStackProps
  ) {
    super(scope, id, props);

    // SSM parameter names/ARNs are fully deterministic from envPrefix +
    // account + region, so no cross-stack export/import is needed for them
    // (same approach as ApiPublishmentStack).
    const rateLimitFiveHourParamName = rateLimitParamName(
      props.envPrefix,
      "five-hour"
    );
    const rateLimitSevenDayParamName = rateLimitParamName(
      props.envPrefix,
      "seven-day"
    );
    const rateLimitFiveHourParamArn = `arn:aws:ssm:${Stack.of(this).region}:${
      Stack.of(this).account
    }:parameter${rateLimitFiveHourParamName}`;
    const rateLimitSevenDayParamArn = `arn:aws:ssm:${Stack.of(this).region}:${
      Stack.of(this).account
    }:parameter${rateLimitSevenDayParamName}`;

    const curAnalysis = new ClaudeCodeCurAnalysis(this, "CurAnalysis", {
      envPrefix: props.envPrefix,
    });

    const syncHandlerRole = new iam.Role(this, "SyncHandlerRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    syncHandlerRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        "service-role/AWSLambdaBasicExecutionRole"
      )
    );
    syncHandlerRole.addToPolicy(
      // Assume the main stack's role for usage-ledger writes and Deny
      // policy attach/detach (see database.ts's ClaudeCodeCostSyncRole).
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [props.claudeCodeCostSyncRoleArn],
      })
    );
    syncHandlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
        ],
        resources: [curAnalysis.workgroupArn],
      })
    );
    syncHandlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartitions",
        ],
        resources: [
          `arn:aws:glue:${Stack.of(this).region}:${
            Stack.of(this).account
          }:catalog`,
          `arn:aws:glue:${Stack.of(this).region}:${
            Stack.of(this).account
          }:database/${curAnalysis.database.databaseName}`,
          `arn:aws:glue:${Stack.of(this).region}:${
            Stack.of(this).account
          }:table/${curAnalysis.database.databaseName}/*`,
        ],
      })
    );
    curAnalysis.curBucket.grantRead(syncHandlerRole);
    curAnalysis.resultOutputBucket.grantReadWrite(syncHandlerRole);
    syncHandlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter"],
        resources: [rateLimitFiveHourParamArn, rateLimitSevenDayParamArn],
      })
    );
    const notificationTopic = sns.Topic.fromTopicArn(
      this,
      "NotificationTopic",
      props.claudeCodeNotificationTopicArn
    );
    notificationTopic.grantPublish(syncHandlerRole);

    const syncHandler = new python.PythonFunction(this, "SyncHandler", {
      entry: path.join(__dirname, "../../backend/claude_code_cost_sync/"),
      runtime: Runtime.PYTHON_3_13,
      timeout: Duration.minutes(5),
      role: syncHandlerRole,
      environment: {
        CLAUDE_CODE_COST_SYNC_ROLE_ARN: props.claudeCodeCostSyncRoleArn,
        USAGE_LEDGER_TABLE_NAME: props.usageLedgerTableName,
        CLAUDE_CODE_IAM_USER_TABLE_NAME: props.claudeCodeIamUserTableName,
        GLUE_DATABASE_NAME: curAnalysis.database.databaseName,
        ATHENA_WORKGROUP: curAnalysis.workgroupName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: rateLimitFiveHourParamName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: rateLimitSevenDayParamName,
        CLAUDE_CODE_NOTIFICATION_TOPIC_ARN: props.claudeCodeNotificationTopicArn,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
      bundling: {
        bundlingFileAccess: BundlingFileAccess.VOLUME_COPY,
      },
    });

    // CUR data itself is refreshed only a few times a day at most, so
    // syncing hourly is frequent enough to keep detection latency low
    // without adding meaningful Athena cost.
    new events.Rule(this, "ScheduleRule", {
      schedule: events.Schedule.rate(Duration.hours(1)),
      targets: [new targets.LambdaFunction(syncHandler)],
    });

    new CfnOutput(this, "ClaudeCodeCostSyncHandlerName", {
      value: syncHandler.functionName,
    });
  }
}
