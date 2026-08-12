#!/usr/bin/env python3
"""Tear down all AWS resources created for the herdr-on-AgentCore smoke test.

Deletes (in order): AgentCore runtime, IAM role (inline policies first),
ECR repository (--force, removes images too).

Usage: AWS_PROFILE=herdr-agentcore-deploy python3 cleanup.py
"""
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

PROFILE = "herdr-agentcore-deploy"
REGION = "us-east-1"
REPO_NAME = "herdr-agentcore-smoke"
ROLE_NAME = "herdr-agentcore-smoke-execution-role"

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
agentcore_control = session.client("bedrock-agentcore-control")
iam = session.client("iam")
ecr = session.client("ecr")


def delete_runtime():
    with open("deploy_state.json") as f:
        state = json.load(f)
    arn = state["agentRuntimeArn"]
    runtime_id = arn.rsplit("/", 1)[-1]
    try:
        agentcore_control.delete_agent_runtime(agentRuntimeId=runtime_id)
        print(f"Delete requested for AgentCore runtime: {runtime_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            print(f"AgentCore runtime already gone: {runtime_id}")
        else:
            raise
    return runtime_id


def delete_role():
    try:
        policies = iam.list_role_policies(RoleName=ROLE_NAME)["PolicyNames"]
        for policy_name in policies:
            iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=policy_name)
            print(f"Deleted inline policy: {policy_name}")

        attached = iam.list_attached_role_policies(RoleName=ROLE_NAME)["AttachedPolicies"]
        for policy in attached:
            iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn=policy["PolicyArn"])
            print(f"Detached policy: {policy['PolicyArn']}")

        iam.delete_role(RoleName=ROLE_NAME)
        print(f"Deleted IAM role: {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"IAM role already gone: {ROLE_NAME}")
        else:
            raise


def delete_ecr_repo():
    try:
        ecr.delete_repository(repositoryName=REPO_NAME, force=True)
        print(f"Deleted ECR repo (force, images included): {REPO_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            print(f"ECR repo already gone: {REPO_NAME}")
        else:
            raise


def verify_gone(runtime_id):
    print("\n=== Independent verification ===")
    try:
        agentcore_control.get_agent_runtime(agentRuntimeId=runtime_id)
        print(f"WARNING: runtime {runtime_id} still exists")
    except ClientError as e:
        print(f"Runtime verified gone: {e.response['Error']['Code']}")

    try:
        iam.get_role(RoleName=ROLE_NAME)
        print(f"WARNING: role {ROLE_NAME} still exists")
    except ClientError as e:
        print(f"Role verified gone: {e.response['Error']['Code']}")

    try:
        ecr.describe_repositories(repositoryNames=[REPO_NAME])
        print(f"WARNING: repo {REPO_NAME} still exists")
    except ClientError as e:
        print(f"ECR repo verified gone: {e.response['Error']['Code']}")


def main():
    runtime_id = delete_runtime()
    print("Waiting 10s for runtime deletion to propagate...")
    time.sleep(10)
    delete_role()
    delete_ecr_repo()
    verify_gone(runtime_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
