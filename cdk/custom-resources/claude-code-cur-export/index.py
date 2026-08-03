import boto3
import cfnresponse

# The Data Exports (BCM Data Exports) control plane only has a us-east-1
# endpoint, and the AWS::BCMDataExports::Export CloudFormation resource type
# is likewise only registered in us-east-1 -- deploying this stack to any
# other region fails with "Unrecognized resource types". A Lambda-backed
# custom resource has no such restriction: it can call any region's
# endpoint regardless of which region the Lambda itself runs in.
bcm = boto3.client("bcm-data-exports", region_name="us-east-1")


def handler(event, context):
    """Custom resource wrapping bcm-data-exports CreateExport/UpdateExport/
    DeleteExport, since the native CFN resource type isn't usable outside
    us-east-1."""
    request_type = event["RequestType"]
    props = event["ResourceProperties"]
    physical_resource_id = event.get("PhysicalResourceId") or props["ExportName"]

    try:
        if request_type == "Create":
            response = bcm.create_export(Export=_export_definition(props))
            export_arn = response["ExportArn"]
            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"ExportArn": export_arn},
                export_arn,
            )

        elif request_type == "Update":
            bcm.update_export(
                ExportArn=physical_resource_id, Export=_export_definition(props)
            )
            cfnresponse.send(
                event,
                context,
                cfnresponse.SUCCESS,
                {"ExportArn": physical_resource_id},
                physical_resource_id,
            )

        elif request_type == "Delete":
            # Best-effort: if Create never actually succeeded (e.g. it
            # failed before receiving a real ExportArn back),
            # physical_resource_id is just the export name, not an ARN --
            # there is nothing to delete. Never let Delete fail: an
            # unhandled exception here blocks CloudFormation rollback/stack
            # deletion entirely.
            if physical_resource_id.startswith("arn:"):
                try:
                    bcm.delete_export(ExportArn=physical_resource_id)
                except Exception as err:
                    print(f"Failed to delete export '{physical_resource_id}': {err}")
            cfnresponse.send(
                event, context, cfnresponse.SUCCESS, None, physical_resource_id
            )

    except Exception as err:
        print(err)
        cfnresponse.send(event, context, cfnresponse.FAILED, None, physical_resource_id)


def _export_definition(props):
    return {
        "Name": props["ExportName"],
        "DataQuery": {
            "QueryStatement": props["QueryStatement"],
            "TableConfigurations": props["TableConfigurations"],
        },
        "DestinationConfigurations": {
            "S3Destination": {
                "S3Bucket": props["S3Bucket"],
                "S3BucketOwner": props["S3BucketOwner"],
                "S3Prefix": props["S3Prefix"],
                "S3Region": props["S3Region"],
                "S3OutputConfigurations": props["S3OutputConfigurations"],
            }
        },
        "RefreshCadence": {"Frequency": "SYNCHRONOUS"},
    }
