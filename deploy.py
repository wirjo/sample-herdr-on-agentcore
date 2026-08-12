#!/usr/bin/env python3
"""Deploy herdr to AWS Bedrock AgentCore Runtime.

Creates (if needed): ECR repo, pushes local image, IAM execution role,
AgentCore runtime (PUBLIC network mode, no VPC/EFS/S3-Files).

Usage:
    python3 deploy.py                                   # uses default credential chain
    python3 deploy.py --profile myprofile --region us-west-2
"""
import argparse
import base64
import json
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"
DEFAULT_REPO_NAME = "herdr-agentcore-sample"
DEFAULT_LOCAL_IMAGE = "herdr-agentcore-sample:latest"
DEFAULT_RUNTIME_NAME = "herdr_agentcore_sample"
DEFAULT_ROLE_NAME = "herdr-agentcore-sample-execution-role"


def parse_args():
    parser = argparse.ArgumentParser(description="Deploy herdr to AWS Bedrock AgentCore Runtime.")
    parser.add_argument("--profile", default=None, help="AWS profile (default: current credential chain)")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help="ECR repository name")
    parser.add_argument("--local-image", default=DEFAULT_LOCAL_IMAGE, help="Local docker image tag to push")
    parser.add_argument("--runtime-name", default=DEFAULT_RUNTIME_NAME, help="AgentCore runtime name")
    parser.add_argument("--role-name", default=DEFAULT_ROLE_NAME, help="IAM execution role name")
    parser.add_argument(
        "--state-file", default="deploy_state.json", help="Where to write deploy state (default: deploy_state.json)"
    )
    return parser.parse_args()


def get_account_id(sts):
    identity = sts.get_caller_identity()
    print(f"Identity: {identity['Arn']}")
    return identity["Account"]


def ensure_ecr_repo(ecr, repo_name):
    try:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
        uri = resp["repositories"][0]["repositoryUri"]
        print(f"ECR repo exists: {uri}")
        return uri
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        resp = ecr.create_repository(repositoryName=repo_name)
        uri = resp["repository"]["repositoryUri"]
        print(f"Created ECR repo: {uri}")
        return uri


def push_image(ecr, repo_uri, local_image):
    auth = ecr.get_authorization_token()
    token = auth["authorizationData"][0]["authorizationToken"]
    username, password = base64.b64decode(token).decode().split(":", 1)
    endpoint = auth["authorizationData"][0]["proxyEndpoint"]

    login = subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", endpoint],
        input=password.encode(),
        capture_output=True,
    )
    if login.returncode != 0:
        print(login.stdout.decode())
        print(login.stderr.decode())
        raise RuntimeError("docker login failed")
    print("docker login OK")

    remote_tag = f"{repo_uri}:latest"
    subprocess.run(["docker", "tag", local_image, remote_tag], check=True)
    push = subprocess.run(["docker", "push", remote_tag], capture_output=True, text=True)
    print(push.stdout[-2000:])
    if push.returncode != 0:
        print(push.stderr[-2000:])
        raise RuntimeError("docker push failed")
    print(f"Pushed: {remote_tag}")
    return remote_tag


def ensure_execution_role(iam, account_id, region, role_name, repo_name):
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    try:
        resp = iam.get_role(RoleName=role_name)
        role_arn = resp["Role"]["Arn"]
        print(f"IAM role exists: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for herdr-on-AgentCore sample",
        )
        role_arn = resp["Role"]["Arn"]
        print(f"Created IAM role: {role_arn}")

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                "Resource": f"arn:aws:ecr:{region}:{account_id}:repository/{repo_name}",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                ],
                "Resource": "*",
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="herdr-agentcore-sample-execution-policy",
        PolicyDocument=json.dumps(inline_policy),
    )
    print("Attached/updated inline execution policy")

    print("Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return role_arn


def create_runtime(agentcore_control, runtime_name, image_uri, role_arn):
    try:
        existing = agentcore_control.list_agent_runtimes()
        for rt in existing.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == runtime_name:
                arn = rt["agentRuntimeArn"]
                print(f"AgentCore runtime already exists: {arn}")
                return arn
    except ClientError as e:
        print(f"list_agent_runtimes check failed (continuing): {e}")

    resp = agentcore_control.create_agent_runtime(
        agentRuntimeName=runtime_name,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
    )
    arn = resp["agentRuntimeArn"]
    print(f"Created AgentCore runtime: {arn}")
    print(f"Status: {resp.get('status')}")
    return arn


def wait_for_ready(agentcore_control, arn, timeout=300):
    runtime_id = arn.rsplit("/", 1)[-1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = agentcore_control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status")
        print(f"Runtime status: {status}")
        if status in ("READY", "ACTIVE"):
            return resp
        if status in ("CREATE_FAILED", "FAILED"):
            raise RuntimeError(f"Runtime failed to become ready: {resp}")
        time.sleep(10)
    raise TimeoutError(f"Runtime did not become ready within {timeout}s")


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sts = session.client("sts")
    ecr = session.client("ecr")
    iam = session.client("iam")
    agentcore_control = session.client("bedrock-agentcore-control")

    account_id = get_account_id(sts)
    repo_uri = ensure_ecr_repo(ecr, args.repo_name)
    image_uri = push_image(ecr, repo_uri, args.local_image)
    role_arn = ensure_execution_role(iam, account_id, args.region, args.role_name, args.repo_name)
    arn = create_runtime(agentcore_control, args.runtime_name, image_uri, role_arn)
    wait_for_ready(agentcore_control, arn)

    state = {
        "agentRuntimeArn": arn,
        "roleArn": role_arn,
        "repoUri": repo_uri,
        "imageUri": image_uri,
        "region": args.region,
    }
    with open(args.state_file, "w") as f:
        json.dump(state, f, indent=2)

    print("\n=== DEPLOY COMPLETE ===")
    print(json.dumps(state, indent=2))
    print(f"\nNext: python3 attach.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
