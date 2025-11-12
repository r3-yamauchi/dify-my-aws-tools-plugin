# Privacy Policy

This document describes how the **my_aws_tools** plugin for Dify handles data when you enable its tools. The plugin is designed to interact with AWS services (such as Bedrock, SageMaker, Lambda, Transcribe, OpenSearch, S3, and DynamoDB) on your behalf. It does not collect analytics or telemetry beyond what is required to fulfill the tool invocations you issue.

## Data Collection
- **User-supplied inputs.** Text prompts, speech/audio URLs, translation requests, Lambda payloads, and other parameters that you pass to the tools are sent to the corresponding AWS service only for the purpose of executing that tool invocation.
- **Configuration metadata.** Optional AWS credentials (access key, secret key, region) may be provided either at the provider level or per tool. These values stay within the plugin runtime and are forwarded solely to AWS SDK clients to authenticate requests.
- **Generated outputs.** Responses received from AWS (e.g., transcription text, Bedrock retrieve results, SageMaker inference outputs, browser-session information) are returned directly to Dify and are not stored elsewhere by this plugin.
- The plugin does **not** collect personally identifiable information unless included in the data that you explicitly send to the tools.

## Data Usage
- Inputs are transmitted to AWS services strictly to execute the selected tool (e.g., running Transcribe, retrieving from Bedrock KB, generating Nova images/videos, reranking documents).
- Outputs from AWS are returned to the Dify workflow or agent as-is. No secondary processing or analysis is performed beyond light formatting necessary for the Dify UI.
- The plugin does not sell, share, or reuse your data for any other purpose. Data is not used for model training by this plugin.

## Data Storage
- By default, the plugin does **not** store any user inputs or outputs on its own disk.
- Temporary files (e.g., downloaded GIFs for frame extraction) are written to local storage only for the duration of the request and deleted immediately after completion.
- Any persistent storage happens only when you instruct a tool to do so (e.g., writing a file to S3 or DynamoDB via the respective tools). In such cases the data resides in your AWS account under the resources you control.
- **Exception – AgentCore Browser Session Manager.** To reopen Playwright browser sessions without re-authenticating, this tool writes minimal session metadata (session ID, WebSocket endpoint, serialized headers/tokens required by AgentCore) to AWS Systems Manager Parameter Store in *your* AWS account. These parameters never leave your account, contain no page content or prompts, and are deleted automatically when you call `close_browser_session` or when the stored TTL expires. If you prefer not to retain this metadata, disable the browser session manager or periodically purge the corresponding Parameter Store path.

## Third-party Services
- The plugin communicates exclusively with AWS services using the official AWS SDK (boto3) and, for browser automation, the Bedrock AgentCore Browser service plus Playwright. No other third-party APIs are contacted.
- When using OpenSearch, SageMaker, Bedrock, Lambda, Transcribe, Comprehend, S3, or DynamoDB tools, the data is transmitted directly to those AWS endpoints over HTTPS.
- Browser tooling stores connection metadata (WebSocket URLs, headers) in AWS Systems Manager Parameter Store in your account so that sessions can be reused. These parameters contain no additional user data beyond what is required to connect.

## Security
- All network calls to AWS services use HTTPS, and AWS credentials are loaded into boto3 clients only when needed. If you provide credentials via the provider settings, they remain in memory within the plugin runtime and are not persisted.
- Parameter Store entries created for AgentCore Browser sessions are stored in your AWS account and inherit the IAM policies you configure.
- The browser tool caches Playwright sessions in memory only for the life of the plugin process and cleans up resources when sessions are closed.
- Temporary files for media processing are stored under the plugin workspace with restrictive permissions and are deleted after each request.
- It is your responsibility to secure your AWS resources (IAM policies, S3 bucket ACLs, DynamoDB tables, etc.). The plugin will operate with whatever permissions the provided credentials allow.

If you have questions or would like to report a privacy concern, please open an issue in your fork or contact the maintainer directly.
