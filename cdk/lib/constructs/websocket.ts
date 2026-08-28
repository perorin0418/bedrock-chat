import { Construct } from "constructs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { WebSocketLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";

import {
  DockerImageCode,
  DockerImageFunction,
  IFunction,
} from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import * as iam from "aws-cdk-lib/aws-iam";
import { CfnOutput, Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import { Auth } from "./auth";
import { ITable } from "aws-cdk-lib/aws-dynamodb";
import { CfnRouteResponse } from "aws-cdk-lib/aws-apigatewayv2";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { excludeDockerImage } from "../constants/docker";
import { Platform } from "aws-cdk-lib/aws-ecr-assets";
import { Database } from "./database";

export interface WebSocketProps {
  readonly database: Database;
  readonly rateLimitFiveHourParam: ssm.IStringParameter;
  readonly rateLimitSevenDayParam: ssm.IStringParameter;
  readonly auth: Auth;
  readonly bedrockRegion: string;
  readonly documentBucket: s3.IBucket;
  readonly largeMessageBucket: s3.IBucket;
  readonly accessLogBucket?: s3.Bucket;
  readonly enableBedrockGlobalInference: boolean;
  readonly enableBedrockCrossRegionInference: boolean;
  readonly enableLambdaSnapStart: boolean;
  readonly mcpOAuthRedirectUri: string;
}

export class WebSocket extends Construct {
  readonly webSocketApi: apigwv2.IWebSocketApi;
  readonly handler: IFunction;
  private readonly defaultStageName = "dev";

  constructor(scope: Construct, id: string, props: WebSocketProps) {
    super(scope, id);

    const { database } = props;
    const { tableAccessRole } = database;

    // Bucket for SNS large payload support
    // See: https://docs.aws.amazon.com/sns/latest/dg/extended-client-library-python.html
    const largePayloadSupportBucket = new s3.Bucket(
      this,
      "LargePayloadSupportBucket",
      {
        encryption: s3.BucketEncryption.S3_MANAGED,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        removalPolicy: RemovalPolicy.DESTROY,
        objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
        autoDeleteObjects: true,
        serverAccessLogsBucket: props.accessLogBucket,
        serverAccessLogsPrefix: "LargePayloadSupportBucket",
      }
    );

    const handlerRole = new iam.Role(this, "HandlerRole", {
      assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    handlerRole.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName(
        "service-role/AWSLambdaBasicExecutionRole"
      )
    );
    handlerRole.addToPolicy(
      // Assume the table access role for row-level access control.
      new iam.PolicyStatement({
        actions: ["sts:AssumeRole"],
        resources: [tableAccessRole.roleArn],
      })
    );
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["bedrock:*"],
        resources: ["*"],
      })
    );
    handlerRole.addToPolicy(
      // Bedrock's Converse operation checks AWS Marketplace entitlement for
      // the calling principal; without these the model access is denied
      // even when the account itself already has model access enabled.
      new iam.PolicyStatement({
        actions: ["aws-marketplace:ViewSubscriptions", "aws-marketplace:Subscribe"],
        resources: ["*"],
      })
    );
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ["cognito-idp:AdminListGroupsForUser"],
        resources: [props.auth.userPool.userPoolArn],
      })
    );

    // get api key from secrets manager (read-only: firecrawl/mcp static credentials)
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:firecrawl/*/*`,
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:mcp/*/*`,
        ],
      })
    );

    // For MCP oauth tokens: chat-time token refresh (OAuthClientProvider)
    // can rotate/write the stored tokens, so this needs read-write access,
    // unlike the read-only firecrawl/mcp static credentials above.
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          "secretsmanager:CreateSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:PutSecretValue",
          "secretsmanager:UpdateSecret",
          "secretsmanager:TagResource",
        ],
        resources: [
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:mcp-oauth/*/*`,
        ],
      })
    );

    // Read-only: chat-time lookup of the pool token's raw OAuth string
    // (see app/claude_teams/token_secrets.py:get_claude_teams_token),
    // selected via app/claude_teams/token_repository.py against
    // claudeTeamsTokenTable below.
    handlerRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["secretsmanager:GetSecretValue"],
        resources: [
          `arn:aws:secretsmanager:${Stack.of(this).region}:${
            Stack.of(this).account
          }:secret:claude-teams-token/*`,
        ],
      })
    );

    largePayloadSupportBucket.grantRead(handlerRole);
    database.websocketSessionTable.grantReadWriteData(handlerRole);
    props.largeMessageBucket.grantReadWrite(handlerRole);
    props.documentBucket.grantRead(handlerRole);
    props.rateLimitFiveHourParam.grantRead(handlerRole);
    props.rateLimitSevenDayParam.grantRead(handlerRole);
    // Read-write: chat-time token selection (round-robin bookkeeping via
    // LastUsedAt) and cooldown/disable updates on API failures (see
    // app/claude_teams/token_repository.py).
    database.claudeTeamsTokenTable.grantReadWriteData(handlerRole);

    // Packaged as a container image (not a zip) because claude-agent-sdk
    // bundles the Claude Code CLI binary (~230MB), which alone exceeds the
    // 250MiB unzipped-size limit for zip-based Lambda functions once combined
    // with the rest of the backend's dependencies. Container images support
    // up to 10GB. Trade-off: SnapStart is zip/managed-runtime only, so it is
    // not available here (see enableLambdaSnapStart handling below).
    const handler = new DockerImageFunction(this, "HandlerV2", {
      code: DockerImageCode.fromImageAsset(
        path.join(__dirname, "../../../backend"),
        {
          platform: Platform.LINUX_AMD64,
          file: "lambda.Dockerfile",
          cmd: ["app.websocket.handler"],
          exclude: [...excludeDockerImage],
        }
      ),
      memorySize: 512,
      timeout: Duration.minutes(15),
      environment: {
        ACCOUNT: Stack.of(this).account,
        REGION: Stack.of(this).region,
        USER_POOL_ID: props.auth.userPool.userPoolId,
        CLIENT_ID: props.auth.client.userPoolClientId,
        BEDROCK_REGION: props.bedrockRegion,
        CONVERSATION_TABLE_NAME: database.conversationTable.tableName,
        BOT_TABLE_NAME: database.botTable.tableName,
        TABLE_ACCESS_ROLE_ARN: tableAccessRole.roleArn,
        LARGE_MESSAGE_BUCKET: props.largeMessageBucket.bucketName,
        LARGE_PAYLOAD_SUPPORT_BUCKET: largePayloadSupportBucket.bucketName,
        WEBSOCKET_SESSION_TABLE_NAME: database.websocketSessionTable.tableName,
        USAGE_LEDGER_TABLE_NAME: database.usageLedgerTable.tableName,
        CLAUDE_TEAMS_TOKEN_TABLE_NAME: database.claudeTeamsTokenTable.tableName,
        RATE_LIMIT_FIVE_HOUR_PARAM_NAME: props.rateLimitFiveHourParam.parameterName,
        RATE_LIMIT_SEVEN_DAY_PARAM_NAME: props.rateLimitSevenDayParam.parameterName,
        ENABLE_BEDROCK_GLOBAL_INFERENCE:
          props.enableBedrockGlobalInference.toString(),
        ENABLE_BEDROCK_CROSS_REGION_INFERENCE:
          props.enableBedrockCrossRegionInference.toString(),
        USE_STRANDS: "true",
        MCP_OAUTH_REDIRECT_URI: props.mcpOAuthRedirectUri,
      },
      role: handlerRole,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    const webSocketApi = new apigwv2.WebSocketApi(this, "WebSocketApi", {
      connectRouteOptions: {
        integration: new WebSocketLambdaIntegration(
          "ConnectIntegration",
          handler.currentVersion
        ),
      },
    });
    const route = webSocketApi.addRoute("$default", {
      integration: new WebSocketLambdaIntegration(
        "DefaultIntegration",
        handler.currentVersion
      ),
    });
    new apigwv2.WebSocketStage(this, "WebSocketStage", {
      webSocketApi,
      stageName: this.defaultStageName,
      autoDeploy: true,
    });
    webSocketApi.grantManageConnections(handler);

    new CfnRouteResponse(this, "RouteResponse", {
      apiId: webSocketApi.apiId,
      routeId: route.routeId,
      routeResponseKey: "$default",
    });

    this.webSocketApi = webSocketApi;
    this.handler = handler;

    new CfnOutput(this, "WebSocketEndpoint", {
      value: this.apiEndpoint,
    });
  }

  get apiEndpoint() {
    return `${this.webSocketApi.apiEndpoint}/${this.defaultStageName}`;
  }
}
