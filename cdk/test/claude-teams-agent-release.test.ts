import * as cdk from "aws-cdk-lib";
import { Template, Match } from "aws-cdk-lib/assertions";
import { BedrockChatStack } from "../lib/bedrock-chat-stack";
import { BedrockRegionResourcesStack } from "../lib/bedrock-region-resources";

/**
 * Synth-level checks for the Claude Teams member-agent release channel
 * (see scripts/claude_teams_member_agent_go/updater.go and
 * backend/app/claude_teams/agent_release_repository.py).
 *
 * Kept in its own file, with asset bundling switched off via the
 * `aws:cdk:bundling-stacks` context, because nothing here depends on
 * Lambda asset contents -- only on bucket properties, an IAM grant and a
 * route. cdk.test.ts instantiates this stack many times and bundles
 * every Python/Docker asset each time, which is slow enough to dominate
 * a run; disabling bundling makes these assertions take seconds.
 */
describe("Claude Teams agent release channel", () => {
  const buildStack = () => {
    const app = new cdk.App();
    // Empty list = bundle nothing; assets become placeholders.
    app.node.setContext("aws:cdk:bundling-stacks", []);

    const bedrockRegionResourcesStack = new BedrockRegionResourcesStack(
      app,
      "BedrockRegionResourcesStack",
      { env: { region: "us-east-1" }, crossRegionReferences: true }
    );

    // Props mirror cdk.test.ts's "MyTestStack" so this exercises the
    // same configuration the main suite does.
    return new BedrockChatStack(app, "MyTestStack", {
      env: { region: "us-west-2" },
      envName: "test",
      envPrefix: "test-",
      bedrockRegion: "us-east-1",
      crossRegionReferences: true,
      webAclId: "",
      identityProviders: [],
      userPoolDomainPrefix: "",
      publishedApiAllowedIpV4AddressRanges: [""],
      publishedApiAllowedIpV6AddressRanges: [""],
      allowedSignUpEmailDomains: [],
      autoJoinUserGroups: [],
      selfSignUpEnabled: true,
      requireAdminApproval: false,
      enableIpV6: true,
      allowedIpV4AddressRanges: [""],
      allowedIpV6AddressRanges: [""],
      documentBucket: bedrockRegionResourcesStack.documentBucket,
      enableRagReplicas: false,
      enableBedrockGlobalInference: false,
      enableBedrockCrossRegionInference: false,
      enableLambdaSnapStart: true,
      enableBotStore: true,
      enableBotStoreReplicas: false,
      botStoreLanguage: "en",
      tokenValidMinutes: 60,
    });
  };

  let template: Template;
  beforeAll(() => {
    template = Template.fromStack(buildStack());
  });

  // The released .exe carries the org-wide Registration Secret baked in
  // at build time, so a publicly readable object here would hand that
  // secret to anyone who learns the URL.
  test("release bucket blocks all public access and is versioned", () => {
    template.hasResourceProperties("AWS::S3::Bucket", {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      },
      VersioningConfiguration: { Status: "Enabled" },
      BucketEncryption: Match.anyValue(),
    });
  });

  // Rolling back means pointing the manifest at an older key, so
  // previously published objects must survive both a stack teardown and
  // an accidental same-key overwrite.
  test("release bucket is retained on stack deletion", () => {
    const buckets = template.findResources("AWS::S3::Bucket", {
      Properties: { VersioningConfiguration: { Status: "Enabled" } },
    });
    const retained = Object.entries(buckets).filter(
      ([id, bucket]: [string, any]) =>
        id.includes("ClaudeTeamsAgentRelease") &&
        bucket.DeletionPolicy === "Retain"
    );
    expect(retained).toHaveLength(1);
  });

  test("release bucket enforces SSL", () => {
    template.hasResourceProperties("AWS::S3::BucketPolicy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: "s3:*",
            Effect: "Deny",
            Condition: { Bool: { "aws:SecureTransport": "false" } },
          }),
        ]),
      },
    });
  });

  // Releases are published out-of-band by an admin, never by the API
  // Lambda, so write access would only add the ability for a
  // compromised handler to serve members a binary of its own choosing.
  test("api handler is granted S3 read, not write, on the release bucket", () => {
    const policies = template.findResources("AWS::IAM::Policy");
    let sawRead = false;

    for (const policy of Object.values(policies) as any[]) {
      for (const statement of policy.Properties.PolicyDocument.Statement) {
        const resources = JSON.stringify(statement.Resource ?? "");
        if (!resources.includes("ClaudeTeamsAgentReleaseBucket")) continue;

        const actions: string[] = ([] as string[]).concat(
          statement.Action ?? []
        );
        const writes = actions.filter(
          (action) =>
            action.startsWith("s3:Put") ||
            action.startsWith("s3:Delete") ||
            action.startsWith("s3:Write") ||
            action === "s3:*"
        );
        expect(writes).toEqual([]);

        if (actions.some((action) => action.startsWith("s3:Get"))) {
          sawRead = true;
        }
      }
    }

    expect(sawRead).toBe(true);
  });

  test("api handler receives the release bucket name in its environment", () => {
    const functions = template.findResources("AWS::Lambda::Function");
    const configured = Object.values(functions).some((fn: any) =>
      Object.keys(fn.Properties?.Environment?.Variables ?? {}).includes(
        "CLAUDE_TEAMS_AGENT_RELEASE_BUCKET"
      )
    );
    expect(configured).toBe(true);
  });

  // Must bypass the Cognito authorizer (the caller is a member's
  // machine, not a frontend session) and be POST so the ingest_secret
  // stays out of access logs.
  test("agent-version route is POST and not Cognito-authorized", () => {
    const routes = template.findResources("AWS::ApiGatewayV2::Route", {
      Properties: {
        RouteKey: "POST /claude-teams-tokens/{token_id}/agent-version",
      },
    });
    const matched = Object.values(routes) as any[];

    expect(matched).toHaveLength(1);
    expect(matched[0].Properties.AuthorizerId).toBeUndefined();
  });

  test("the pre-existing claude-teams routes are unchanged", () => {
    for (const routeKey of [
      "POST /claude-teams-tokens/register",
      "POST /claude-teams-tokens/{token_id}/usage-snapshot",
      "GET /claude-teams-tokens/{token_id}/status",
    ]) {
      template.hasResourceProperties("AWS::ApiGatewayV2::Route", {
        RouteKey: routeKey,
      });
    }
  });

  // An admin needs the bucket name to publish a release at all.
  test("the release bucket name is exported as a stack output", () => {
    const outputs = template.findOutputs("*");
    const exported = Object.keys(outputs).some((key) =>
      key.includes("ClaudeTeamsAgentReleaseBucketName")
    );
    expect(exported).toBe(true);
  });
});
