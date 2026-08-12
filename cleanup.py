#!/usr/bin/env python3
"""Tear down all AWS resources created by deploy.py.

Deletes (in order): AgentCore runtime, IAM role (inline policies first),
ECR repository (--force, removes images too).

Usage:
    python3 cleanup.py                                   # uses default credential chain
    python3 cleanup.py --profile myprofile --region us-west-2
"""
import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "us-east-1"
DEFAULT_REPO_NAME = "herdr-agentcore-sample"
DEFAULT_ROLE_NAME = "herdr-agentcore-sample-execution-role"


def parse_args():
    parser = argparse.ArgumentParser(description="Tear down AWS resources created by deploy.py.")
    parser.add_argument("--profile", default=None, help="AWS profile (default: current credential chain)")
    parser.add_argument("--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help="ECR repository name")
    parser.add_argument("--role-name", default=DEFAULT_ROLE_NAME, help="IAM execution role name")
    parser.add_argument(
        "--state-file", default="deploy_state.json", help="Deploy state file written by deploy.py"
    )
    return parser.parse_args()


def delete_runtime(agentcore_control, state_file):
    with open(state_file) as f:
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


def delete_role(iam, role_name):
    try:
        policies = iam.list_role_policies(RoleName=role_name)["PolicyNames"]
        for policy_name in policies:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            print(f"Deleted inline policy: {policy_name}")

        attached = iam.list_attached_role_policies(RoleName=role_name)["AttachedPolicies"]
        for policy in attached:
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
            print(f"Detached policy: {policy['PolicyArn']}")

        iam.delete_role(RoleName=role_name)
        print(f"Deleted IAM role: {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            print(f"IAM role already gone: {role_name}")
        else:
            raise


def delete_ecr_repo(ecr, repo_name):
    try:
        ecr.delete_repository(repositoryName=repo_name, force=True)
        print(f"Deleted ECR repo (force, images included): {repo_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            print(f"ECR repo already gone: {repo_name}")
        else:
            raise


def verify_gone(agentcore_control, iam, ecr, runtime_id, role_name, repo_name):
    print("\n=== Independent verification ===")
    try:
        agentcore_control.get_agent_runtime(agentRuntimeId=runtime_id)
        print(f"WARNING: runtime {runtime_id} still exists")
    except ClientError as e:
        print(f"Runtime verified gone: {e.response['Error']['Code']}")

    try:
        iam.get_role(RoleName=role_name)
        print(f"WARNING: role {role_name} still exists")
    except ClientError as e:
        print(f"Role verified gone: {e.response['Error']['Code']}")

    try:
        ecr.describe_repositories(repositoryNames=[repo_name])
        print(f"WARNING: repo {repo_name} still exists")
    except ClientError as e:
        print(f"ECR repo verified gone: {e.response['Error']['Code']}")


def main():
    args = parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    agentcore_control = session.client("bedrock-agentcore-control")
    iam = session.client("iam")
    ecr = session.client("ecr")

    runtime_id = delete_runtime(agentcore_control, args.state_file)
    print("Waiting 10s for runtime deletion to propagate...")
    time.sleep(10)
    delete_role(iam, args.role_name)
    delete_ecr_repo(ecr, args.repo_name)
    verify_gone(agentcore_control, iam, ecr, runtime_id, args.role_name, args.repo_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
