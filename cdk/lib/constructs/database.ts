import { CfnOutput, RemovalPolicy, Stack } from "aws-cdk-lib";
import {
  AttributeType,
  BillingMode,
  Table,
  TableEncryption,
  StreamViewType,
} from "aws-cdk-lib/aws-dynamodb";
import { AccountPrincipal, PolicyStatement, Role } from "aws-cdk-lib/aws-iam";
import { Construct } from "constructs";

export interface DatabaseProps {
  pointInTimeRecovery?: boolean;
}

export class Database extends Construct {
  readonly conversationTable: Table;
  readonly botTable: Table;
  readonly tableAccessRole: Role;
  readonly websocketSessionTable: Table;
  readonly usageLedgerTable: Table;
  readonly apiKeyOwnerTable: Table;
  readonly claudeCodeIamUserTable: Table;
  readonly claudeCodeCostSyncRole: Role;

  constructor(scope: Construct, id: string, props?: DatabaseProps) {
    super(scope, id);

    // Conversation Table
    const conversationTable = new Table(this, "ConversationTableV3", {
      // PK: UserId
      partitionKey: { name: "PK", type: AttributeType.STRING },
      // SK: ConversationId
      sortKey: { name: "SK", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      stream: StreamViewType.NEW_IMAGE,
      pointInTimeRecovery: props?.pointInTimeRecovery,
      encryption: TableEncryption.AWS_MANAGED,
    });
    conversationTable.addGlobalSecondaryIndex({
      // Used to fetch conversation or bot by id
      indexName: "SKIndex",
      partitionKey: { name: "SK", type: AttributeType.STRING },
    });

    // Bot Table
    const botTable = new Table(this, "BotTableV3", {
      // PK: UserId
      partitionKey: { name: "PK", type: AttributeType.STRING },
      // SK: ItemType
      sortKey: { name: "SK", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      stream: StreamViewType.NEW_IMAGE,
      // Need to enable PITR for bot table for Zero-ETL pipeline
      pointInTimeRecovery: true,
      encryption: TableEncryption.AWS_MANAGED,
    });
    // LSI-1
    botTable.addLocalSecondaryIndex({
      indexName: "StarredIndex",
      sortKey: { name: "IsStarred", type: AttributeType.STRING },
    });
    // LSI-2
    botTable.addLocalSecondaryIndex({
      indexName: "LastUsedTimeIndex",
      sortKey: { name: "LastUsedTime", type: AttributeType.NUMBER },
    });
    // GSI-1
    botTable.addGlobalSecondaryIndex({
      indexName: "BotIdIndex",
      partitionKey: { name: "BotId", type: AttributeType.STRING },
    });
    // GSI-2
    botTable.addGlobalSecondaryIndex({
      indexName: "SharedScopeIndex",
      partitionKey: { name: "SharedScope", type: AttributeType.STRING },
      sortKey: { name: "SharedStatus", type: AttributeType.STRING },
    });
    // GSI-3
    botTable.addGlobalSecondaryIndex({
      indexName: "ItemTypeIndex",
      partitionKey: { name: "ItemType", type: AttributeType.STRING },
    });
    // GSI-4
    botTable.addGlobalSecondaryIndex({
      indexName: "SyncStatusIndex",
      partitionKey: {
        name: "SyncStatus",
        type: AttributeType.STRING,
      },
    });

    // Usage ledger table for cost-based rate limiting.
    // PK: UserId, SK: Timestamp (epoch milliseconds)
    const usageLedgerTable = new Table(this, "UsageLedgerTable", {
      partitionKey: { name: "PK", type: AttributeType.STRING },
      sortKey: { name: "SK", type: AttributeType.NUMBER },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      timeToLiveAttribute: "expire",
      encryption: TableEncryption.AWS_MANAGED,
    });

    // Binds a published-API key to the user who created it, for rate-limit
    // attribution. PK: ApiKeyId. No TTL — rows are deleted explicitly when
    // the key (or its bot's publication) is deleted.
    const apiKeyOwnerTable = new Table(this, "ApiKeyOwnerTable", {
      partitionKey: { name: "ApiKeyId", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      encryption: TableEncryption.AWS_MANAGED,
    });

    // Tracks Claude Code (direct Bedrock access) IAM user provisioning state
    // and Deny-policy status per Bedrock Chat user. PK: UserId. No TTL —
    // offboarding cleanup is a separate, not-yet-implemented task.
    const claudeCodeIamUserTable = new Table(this, "ClaudeCodeIamUserTable", {
      partitionKey: { name: "UserId", type: AttributeType.STRING },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      encryption: TableEncryption.AWS_MANAGED,
    });

    const tableAccessRole = new Role(this, "TableAccessRole", {
      assumedBy: new AccountPrincipal(Stack.of(this).account),
    });
    conversationTable.grantReadWriteData(tableAccessRole);
    botTable.grantReadWriteData(tableAccessRole);
    usageLedgerTable.grantReadWriteData(tableAccessRole);
    apiKeyOwnerTable.grantReadWriteData(tableAccessRole);

    // Assumed once per run by the claude-code-cost-sync Lambda (a trusted
    // batch job writing many users' rows in one execution), unlike
    // tableAccessRole's per-user LeadingKeys row-level security which doesn't
    // fit a batch write pattern.
    const claudeCodeCostSyncRole = new Role(this, "ClaudeCodeCostSyncRole", {
      assumedBy: new AccountPrincipal(Stack.of(this).account),
    });
    usageLedgerTable.grantWriteData(claudeCodeCostSyncRole);
    claudeCodeIamUserTable.grantReadWriteData(claudeCodeCostSyncRole);
    claudeCodeCostSyncRole.addToPolicy(
      new PolicyStatement({
        actions: [
          "iam:PutUserPolicy",
          "iam:DeleteUserPolicy",
          "iam:GetUserPolicy",
        ],
        resources: [
          `arn:aws:iam::${Stack.of(this).account}:user/claude-code-*`,
        ],
      })
    );

    // Websocket session table.
    // This table is used to concatenate user input exceeding 32KB which is the limit of API Gateway.
    const websocketSessionTable = new Table(this, "WebsocketSessionTable", {
      partitionKey: { name: "ConnectionId", type: AttributeType.STRING },
      sortKey: { name: "MessagePartId", type: AttributeType.NUMBER },
      billingMode: BillingMode.PAY_PER_REQUEST,
      removalPolicy: RemovalPolicy.DESTROY,
      timeToLiveAttribute: "expire",
    });

    this.conversationTable = conversationTable;
    this.botTable = botTable;
    this.tableAccessRole = tableAccessRole;
    this.websocketSessionTable = websocketSessionTable;
    this.usageLedgerTable = usageLedgerTable;
    this.apiKeyOwnerTable = apiKeyOwnerTable;
    this.claudeCodeIamUserTable = claudeCodeIamUserTable;
    this.claudeCodeCostSyncRole = claudeCodeCostSyncRole;

    new CfnOutput(this, "ConversationTableName", {
      value: conversationTable.tableName,
    });
    new CfnOutput(this, "BotTableName", {
      value: botTable.tableName,
    });
    new CfnOutput(this, "UsageLedgerTableName", {
      value: usageLedgerTable.tableName,
    });
    new CfnOutput(this, "ApiKeyOwnerTableName", {
      value: apiKeyOwnerTable.tableName,
    });
    new CfnOutput(this, "ClaudeCodeIamUserTableName", {
      value: claudeCodeIamUserTable.tableName,
    });
  }
}
