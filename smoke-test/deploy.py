#!/usr/bin/env python3
"""Deploy herdr smoke-test image to AgentCore Runtime.

Creates (if needed): ECR repo, pushes local image, IAM execution role,
AgentCore runtime (PUBLIC network mode, no VPC/EFS/S3-Files).

Usage: AWS_PROFILE=herdr-agentcore-deploy python3 deploy.py
"""
import base64
import json
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

PROFILE = "herdr-agentcore-deploy"
REGION = "us-east-1"
REPO_NAME = "herdr-agentcore-smoke"
LOCAL_IMAGE = "herdr-agentcore-smoke:latest"
RUNTIME_NAME = "herdr_agentcore_smoke_test"
ROLE_NAME = "herdr-agentcore-smoke-execution-role"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
sts = session.client("sts")
ecr = session.client("ecr")
iam = session.client("iam")
agentcore_control = session.client("bedrock-agentcore-control")


def get_account_id():
    identity = sts.get_caller_identity()
    print(f"Identity: {identity['Arn']} (account {identity['Account']})")
    return identity["Account"]


def ensure_ecr_repo():
    try:
        resp = ecr.describe_repositories(repositoryNames=[REPO_NAME])
        uri = resp["repositories"][0]["repositoryUri"]
        print(f"ECR repo exists: {uri}")
        return uri
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        resp = ecr.create_repository(repositoryName=REPO_NAME)
        uri = resp["repository"]["repositoryUri"]
        print(f"Created ECR repo: {uri}")
        return uri


def push_image(repo_uri):
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
    subprocess.run(["docker", "tag", LOCAL_IMAGE, remote_tag], check=True)
    push = subprocess.run(["docker", "push", remote_tag], capture_output=True, text=True)
    print(push.stdout[-2000:])
    if push.returncode != 0:
        print(push.stderr[-2000:])
        raise RuntimeError("docker push failed")
    print(f"Pushed: {remote_tag}")
    return remote_tag


def ensure_execution_role(account_id):
    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:*"
                    },
                },
            }
        ],
    }

    try:
        resp = iam.get_role(RoleName=ROLE_NAME)
        role_arn = resp["Role"]["Arn"]
        print(f"IAM role exists: {role_arn}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        resp = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for herdr-on-AgentCore smoke test",
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
                "Resource": f"arn:aws:ecr:{REGION}:{account_id}:repository/{REPO_NAME}",
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
        RoleName=ROLE_NAME,
        PolicyName="herdr-smoke-execution-policy",
        PolicyDocument=json.dumps(inline_policy),
    )
    print("Attached/updated inline execution policy")

    # IAM role propagation delay.
    print("Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return role_arn


def create_runtime(image_uri, role_arn):
    try:
        existing = agentcore_control.list_agent_runtimes()
        for rt in existing.get("agentRuntimes", []):
            if rt.get("agentRuntimeName") == RUNTIME_NAME:
                arn = rt["agentRuntimeArn"]
                print(f"AgentCore runtime already exists: {arn}")
                return arn
    except ClientError as e:
        print(f"list_agent_runtimes check failed (continuing): {e}")

    resp = agentcore_control.create_agent_runtime(
        agentRuntimeName=RUNTIME_NAME,
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
    )
    arn = resp["agentRuntimeArn"]
    print(f"Created AgentCore runtime: {arn}")
    print(f"Status: {resp.get('status')}")
    return arn


def wait_for_ready(arn, timeout=300):
    control = agentcore_control
    runtime_id = arn.rsplit("/", 1)[-1]
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = control.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status")
        print(f"Runtime status: {status}")
        if status in ("READY", "ACTIVE"):
            return resp
        if status in ("CREATE_FAILED", "FAILED"):
            raise RuntimeError(f"Runtime failed to become ready: {resp}")
        time.sleep(10)
    raise TimeoutError(f"Runtime did not become ready within {timeout}s")


def main():
    account_id = get_account_id()
    repo_uri = ensure_ecr_repo()
    image_uri = push_image(repo_uri)
    role_arn = ensure_execution_role(account_id)
    arn = create_runtime(image_uri, role_arn)
    final = wait_for_ready(arn)

    state = {
        "agentRuntimeArn": arn,
        "roleArn": role_arn,
        "repoUri": repo_uri,
        "imageUri": image_uri,
        "accountId": account_id,
    }
    with open("deploy_state.json", "w") as f:
        json.dump(state, f, indent=2)

    print("\n=== DEPLOY COMPLETE ===")
    print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
