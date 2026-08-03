import { Construct } from "constructs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as athena from "aws-cdk-lib/aws-athena";
import * as glue from "@aws-cdk/aws-glue-alpha";
import * as awsglue from "aws-cdk-lib/aws-glue";
import * as iam from "aws-cdk-lib/aws-iam";
import { Code, Runtime, Function as LambdaFunction } from "aws-cdk-lib/aws-lambda";
import { CfnOutput, CustomResource, RemovalPolicy, Stack } from "aws-cdk-lib";
import * as path from "path";
import * as fs from "fs";

export interface ClaudeCodeCurAnalysisProps {
  envPrefix: string;
}

/**
 * Creates a CUR 2.0 Data Export (with IAM principal cost allocation) in this
 * same account, plus the Glue/Athena setup to query it. Claude Code's IAM
 * users live in this same account, so a member-account CUR already contains
 * exactly their Bedrock usage — no cross-account bucket sharing needed.
 *
 * The CUR 2.0 Parquet schema is large (125+ possible columns, several
 * nested) and AWS explicitly recommends using a Glue crawler rather than a
 * hand-authored table (see "Setting up Athena manually" / "Processing data
 * exports" in the CUR user guide), so this construct crawls the export
 * instead of declaring columns manually the way UsageAnalysis does for the
 * (small, self-controlled) DynamoDB export schema.
 *
 * The crawler-assigned table name isn't knowable at synth time, so callers
 * must discover it at runtime via `glue:GetTables` on `database.databaseName`
 * rather than assuming a fixed name.
 */
export class ClaudeCodeCurAnalysis extends Construct {
  public readonly database: glue.IDatabase;
  public readonly curBucket: s3.IBucket;
  public readonly resultOutputBucket: s3.IBucket;
  public readonly workgroupName: string;
  public readonly workgroupArn: string;
  public readonly exportName: string;

  constructor(scope: Construct, id: string, props: ClaudeCodeCurAnalysisProps) {
    super(scope, id);

    const safeStackName = Stack.of(this)
      .stackName.toLowerCase()
      .replace(/-/g, "_");
    const GLUE_DATABASE_NAME = `${safeStackName}_claude_code_cur`;
    const sepHyphen = props.envPrefix ? "-" : "";
    const EXPORT_NAME = `${props.envPrefix}${sepHyphen}claude-code-cur`;

    // Bucket for the CUR 2.0 export itself. The account that creates a CUR
    // export must own its destination bucket (no cross-account delivery),
    // which is satisfied trivially here since it's the same account.
    const curBucket = new s3.Bucket(this, "CurBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      autoDeleteObjects: true,
    });

    // Required bucket policy for Data Exports delivery.
    // Ref: https://docs.aws.amazon.com/cur/latest/userguide/dataexports-s3-bucket.html
    // NOTE: the source ARN's region is always us-east-1 -- that's where the
    // bcm-data-exports service itself lives, regardless of which region this
    // bucket (or the stack) is deployed to. See CurExportFunction below.
    curBucket.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "EnableAWSDataExportsToWriteToS3",
        principals: [
          new iam.ServicePrincipal("bcm-data-exports.amazonaws.com"),
        ],
        actions: ["s3:PutObject"],
        resources: [curBucket.arnForObjects("*")],
        conditions: {
          ArnLike: {
            "aws:SourceArn": `arn:aws:bcm-data-exports:us-east-1:${
              Stack.of(this).account
            }:export/*`,
          },
          StringEquals: {
            "aws:SourceAccount": Stack.of(this).account,
          },
        },
      })
    );

    // AWS::BCMDataExports::Export is only registered as a CloudFormation
    // resource type in us-east-1 (bcm-data-exports itself only has a
    // us-east-1 API endpoint), so it can't be declared as a native CFN
    // resource in a stack deployed to any other region. A Lambda-backed
    // custom resource has no such restriction -- it just needs to target
    // us-east-1 in its own boto3 client, regardless of where the Lambda runs.
    const curExportFunction = new LambdaFunction(this, "CurExportFunction", {
      runtime: Runtime.PYTHON_3_13,
      handler: "index.handler",
      code: Code.fromInline(
        fs.readFileSync(
          path.join(
            __dirname,
            "../../custom-resources/claude-code-cur-export/index.py"
          ),
          "utf8"
        )
      ),
    });
    // CreateExport (and the legacy cur:PutReportDefinition bridge permission
    // AWS requires alongside it) doesn't support resource-level scoping --
    // there's no ARN yet at creation time -- so AWS's own docs call for
    // Resource: "*" here. Update/Delete/Get target an existing export, so
    // those stay scoped to this account's export ARNs.
    // Ref: https://aws.amazon.com/blogs/aws-cloud-financial-management/announcing-data-exports-for-focus-1-0-preview-in-aws-billing-and-cost-management/
    curExportFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bcm-data-exports:CreateExport", "cur:PutReportDefinition"],
        resources: ["*"],
      })
    );
    curExportFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "bcm-data-exports:UpdateExport",
          "bcm-data-exports:DeleteExport",
          "bcm-data-exports:GetExport",
        ],
        resources: [
          `arn:aws:bcm-data-exports:us-east-1:${Stack.of(this).account}:export/*`,
        ],
      })
    );

    const curExport = new CustomResource(this, "CurExport", {
      serviceToken: curExportFunction.functionArn,
      resourceType: "Custom::ClaudeCodeCurExport",
      properties: {
        ExportName: EXPORT_NAME,
        // Data Exports rejects "SELECT *" (ValidationException: Invalid
        // QueryStatement) -- columns must be listed explicitly. These are
        // exactly the columns backend/claude_code_cost_sync/index.py's
        // Athena query reads.
        QueryStatement:
          "SELECT line_item_iam_principal, line_item_usage_start_date, " +
          "line_item_unblended_cost, line_item_line_item_type " +
          "FROM COST_AND_USAGE_REPORT",
        TableConfigurations: {
          COST_AND_USAGE_REPORT: {
            TIME_GRANULARITY: "DAILY",
            INCLUDE_IAM_PRINCIPAL_DATA: "TRUE",
          },
        },
        S3Bucket: curBucket.bucketName,
        S3BucketOwner: Stack.of(this).account,
        S3Prefix: EXPORT_NAME,
        S3Region: Stack.of(this).region,
        S3OutputConfigurations: {
          OutputType: "CUSTOM",
          Format: "PARQUET",
          Compression: "PARQUET",
          Overwrite: "OVERWRITE_REPORT",
        },
      },
    });
    // The bucket policy above is a separate CFN resource (BucketPolicy) from
    // the bucket itself; make sure it exists before the export is created.
    if (curBucket.policy) {
      curExport.node.addDependency(curBucket.policy);
    }

    // Athena/Glue setup to query the export, mirroring UsageAnalysis's
    // workgroup + result-bucket pattern.
    const queryResultBucket = new s3.Bucket(this, "QueryResultBucket", {
      encryption: s3.BucketEncryption.S3_MANAGED,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      autoDeleteObjects: true,
    });

    const wg = new athena.CfnWorkGroup(this, "Wg", {
      name: `${safeStackName}_claude_code_cur_wg`,
      description: "Workgroup for querying the Claude Code CUR 2.0 export",
      recursiveDeleteOption: true,
      workGroupConfiguration: {
        resultConfiguration: {
          outputLocation: `s3://${queryResultBucket.bucketName}`,
        },
      },
    });

    const database = new glue.Database(this, "Database", {
      databaseName: GLUE_DATABASE_NAME,
    });

    const crawlerRole = new iam.Role(this, "CrawlerRole", {
      assumedBy: new iam.ServicePrincipal("glue.amazonaws.com"),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          "service-role/AWSGlueServiceRole"
        ),
      ],
    });
    curBucket.grantRead(crawlerRole);

    // CUR 2.0 Data Exports write to <bucket>/<prefix>/<export-name>/data/...
    // Ref: https://docs.aws.amazon.com/cur/latest/userguide/dataexports-processing.html
    const crawler = new awsglue.CfnCrawler(this, "CurCrawler", {
      name: `${safeStackName}_claude_code_cur_crawler`,
      role: crawlerRole.roleArn,
      databaseName: database.databaseName,
      targets: {
        s3Targets: [
          {
            path: `s3://${curBucket.bucketName}/${EXPORT_NAME}/${EXPORT_NAME}/data`,
          },
        ],
      },
      tablePrefix: "claude_code_cur_",
      // Runs daily so newly-appearing month partitions and any upstream
      // schema changes get discovered; CUR itself refreshes at most a few
      // times a day, so more frequent crawling wouldn't find anything new.
      schedule: { scheduleExpression: "cron(0 4 * * ? *)" },
      schemaChangePolicy: {
        updateBehavior: "UPDATE_IN_DATABASE",
        deleteBehavior: "LOG",
      },
    });
    crawler.node.addDependency(curExport);

    this.database = database;
    this.curBucket = curBucket;
    this.resultOutputBucket = queryResultBucket;
    this.workgroupName = wg.name!;
    this.workgroupArn = `arn:aws:athena:*:${
      Stack.of(this).account
    }:workgroup/${wg.name}`;
    this.exportName = EXPORT_NAME;

    new CfnOutput(this, "ClaudeCodeCurDatabaseName", {
      value: database.databaseName,
    });
    new CfnOutput(this, "ClaudeCodeCurWorkgroup", {
      value: wg.name!,
    });
  }
}
