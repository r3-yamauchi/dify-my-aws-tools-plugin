# my_aws_tools

**Author:** r3-yamauchi
**Version:** 1.0.5  
**Type:** tool

English | [Japanese](https://github.com/r3-yamauchi/dify-my-aws-tools-plugin/blob/main/readme/README_ja_JP.md)

## Description

The source code of this plugin is available in the [GitHub repository](https://github.com/r3-yamauchi/dify-my-aws-tools-plugin).

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-my-aws-tools-plugin)

## Fork Status

This repository is a personal fork of the official LangGenius AWS Tools plugin (release 0.0.15) under the Apache License 2.0.

## Overview

My AWS Tools plugin bundles multiple AWS services so that Dify applications can trigger content moderation, document reranking, text-to-speech, speech recognition, and other workflows directly inside the platform.

Included tools:
- Apply Guardrail
- Bedrock Retrieve
- Bedrock Retrieve and Generate
- Bedrock KB List
- Bedrock KB Data Sources
- Bedrock KB Sync
- SNS Publish
- SQS Send Message
- Step Functions Start Execution
- Lambda Invoker
- Lambda YAML to JSON
- Nova Canvas
- Nova Reel
- Extract Frame
- S3 Operator
- S3 File Uploader
- S3 File Download
- S3 List Buckets
- S3 Create Bucket
- S3 List Objects
- CloudFront Create Invalidation
- DynamoDB Manager
- Agentcore Code Interpreter
- Agentcore Memory
- Agentcore Memory Search

## License & Attribution

This project is distributed under the Apache License 2.0. See `LICENSE` for the full text and `NOTICE` for attribution requirements, which also document that this implementation derives from the LangGenius official plugin sources.

## Feature Highlights by Category

### Amazon Bedrock

- **Bedrock Retrieve** – Calls the `bedrock-agent-runtime` Retrieve API to run semantic or hybrid searches against a selected Knowledge Base. You can switch metadata filters, result counts, and Bedrock Reranking models (cohere.rerank-v3-5 / amazon.rerank-v1), and receive outputs as JSON or ranked text.

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "query": "latest product roadmap",
  "search_type": "HYBRID",
  "max_results": 5,
  "reranking_model": "amazon.rerank-v1"
}
```

- **Bedrock Retrieve and Generate** – Wraps `retrieve_and_generate` so KNOWLEDGE_BASE or EXTERNAL_SOURCES flows run in a single call. Supplying `session_configuration` and `session_id` lets Bedrock maintain session state, and the tool returns the text plus citation metadata.

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "query": "Summarize the incident response runbook",
  "generation_configuration": {
    "promptTemplate": "Using the KB, summarize concisely: {{query}}"
  },
  "retrieval_configuration": {
    "vectorSearchConfiguration": {"numberOfResults": 3}
  }
}
```

- **Apply Guardrail** – Uses Bedrock Runtime `apply_guardrail` with these features:
  - Inputs: `content` array (multiple texts and/or images via bytes or S3 URI) or a single `text` that is auto-chunked into 1000-character pieces and wrapped as content.
  - `source`: PREPROCESS (default) or POSTPROCESS to target pre/post LLM stages.
  - Outputs: action, processedOutputs (masked), outputs (raw), assessments, warnings/actionReasons; returned as human-readable text plus a JSON blob.
  - Long-text protection via chunking aligned with Guardrails billing/limits.

#### Apply Guardrail example (multi-text)

```json
{
  "guardrail_id": "gr-123",
  "guardrail_version": "2",
  "source": "PREPROCESS",
  "content": [
    { "text": { "text": "User message 1" } },
    { "text": { "text": "User message 2" } }
  ]
}
```

#### Apply Guardrail example (image + text)

```json
{
  "guardrail_id": "gr-123",
  "guardrail_version": "2",
  "source": "POSTPROCESS",
  "content": [
    {
      "image": {
        "format": "png",
        "source": { "s3Uri": "s3://bucket/path/image.png" }
      }
    },
    { "text": { "text": "LLM generated response" } }
  ]
}
```

- **Nova Canvas** – Invokes Nova Canvas v1 for TEXT_IMAGE, COLOR_GUIDED, IMAGE_VARIATION, INPAINTING, OUTPAINTING, and BACKGROUND_REMOVAL tasks. Input images are fetched from S3 and outputs are uploaded back while also streamed to Dify as PNG blobs.

```json
{
  "task": "TEXT_IMAGE",
  "prompt": "A lighthouse during a storm",
  "output_s3_uri": "s3://my-bucket/outputs/canvas.png"
}
```

- **Nova Reel** – Uses Nova Reel v1 to create videos from text or from a seed image. Results are saved as MP4 files in the specified S3 path, and synchronous mode polls until completion to return the binary.

```json
{
  "mode": "TEXT_TO_VIDEO",
  "prompt": "A drone flyover of snowy mountains",
  "output_s3_uri": "s3://my-bucket/outputs/reel.mp4",
  "wait_for_completion": true
}
```

### Audio & Media Processing

- **Extract Frame** – Downloads GIF animations and extracts evenly spaced PNG frames. Users choose the number of frames (from two for first/last to any higher count), and each frame is returned as binary output.

```json
{
  "gif_url": "https://example.com/anim.gif",
  "frame_count": 4
}
```

- **Lambda YAML to JSON** – Calls a Lambda function synchronously with YAML text in the request body and returns the JSON body only when the Lambda responds with `statusCode` 200.

```yaml
lambda_name: yaml-to-json
yaml_content: |
  key: value
  list:
    - a
    - b
```

- **Bedrock KB List** – Calls `list_knowledge_bases` to enumerate available knowledge bases, returning summaries (status, creation date, vector store) and pagination tokens for downstream filtering.

```json
{
  "max_results": 20
}
```

- **Bedrock KB Data Sources** – Invokes `list_data_sources` for a given knowledge base, returning connector information, synchronization state, and pagination tokens so you can select the correct source before running ingestion jobs.

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "max_results": 10
}
```

- **Bedrock KB Sync** – Calls `StartIngestionJob` for a given knowledge base/data source pair so you can synchronize documents on demand, optionally setting a client token or deletion policy.

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "data_source_id": "ds-001",
  "client_token": "sync-20250227"
}
```

### Storage & Database Operations

- **CloudFront Create Invalidation** – Submits `create_invalidation` for a distribution. Accepts either `paths` (e.g., `["/*"]` or `["/index.html", "/css/*"]`) or an `invalidation_batch` JSON, and optional `caller_reference`; defaults invalidate all paths.

```json
{
  "distribution_id": "D123456"
}
```

```json
{
  "distribution_id": "D123456",
  "paths": ["/index.html", "/css/*"]
}
```

```json
{
  "distribution_id": "D123456",
  "caller_reference": "my-ref-1",
  "invalidation_batch": {
    "Paths": {
      "Items": ["/*"]
    }
  }
}
```

- **S3 File Uploader** – Uploads a workflow file to the specified bucket/key and can return a presigned URL.

```json
{
  "bucket_name": "my-bucket",
  "object_key": "uploads/example.txt",
  "file": "{{file}}",
  "return_presigned_url": true
}
```

- **S3 Operator (write)** – Reads or writes text content to `s3://` URIs; this example writes JSON text.

```json
{
  "operation": "write",
  "s3_uri": "s3://my-bucket/config.json",
  "text": "{\"env\":\"prod\"}"
}
```

- **S3 File Download** – Fetches objects from S3; returns a presigned URL or streams binary (use `presign_only` / `download_mode`).

```json
{
  "bucket_name": "my-bucket",
  "object_key": "reports/latest.pdf",
  "presign_only": true,
  "expires_in": 600
}
```

- **DynamoDB Manager** – Creates PAY_PER_REQUEST tables and supports `put_item` / `get_item` / `delete_item` with JSON `item_data`.

```json
{
  "operation": "put_item",
  "table_name": "users",
  "partition_key_name": "user_id",
  "item_data": {
    "user_id": "u-1",
    "name": "Alice"
  }
}
```

### Messaging

- **SNS Publish** – Publishes to an SNS topic ARN with optional subject and MessageAttributes.

```json
{
  "topic_arn": "arn:aws:sns:us-east-1:111122223333:alerts",
  "message": "Deployed v1.2.3",
  "subject": "Deploy notice"
}
```

- **SQS Send Message** – Sends to an SQS queue URL with optional delay and MessageAttributes.

```json
{
  "queue_url": "https://sqs.us-east-1.amazonaws.com/111122223333/tasks",
  "message_body": "{\"job_id\":123}",
  "delay_seconds": 5
}
```

### AgentCore Integrations

- **Agentcore Code Interpreter** – Creates/uses an interpreter session to run shell commands or code.

```json
{
  "operation": "execute",
  "code": "print(1+1)",
  "language": "python"
}
```

- **AgentCore Memory Search** – Calls `retrieve_memories` for a memory/namespace with top_k limit.

```json
{
  "memory_id": "mem-abc",
  "namespace": "default",
  "query": "error logs",
  "top_k": 5
}
```

- **AgentCore Memory** – Creates memories and records/retrieves turns; supply `operation=record` or `retrieve`.

```json
{
  "operation": "record",
  "memory_id": "mem-123",
  "actor_id": "user",
  "role": "user",
  "content": "Hello!"
}
```

### Other Notes

- **Lambda YAML to JSON** – Lightweight wrapper for reusing your Lambda workloads from workflows.

- **Lambda Invoker** – Calls any Lambda function name or ARN with a JSON payload, optional qualifier, per-call credentials, and tail logs for quick serverless utilities.

```json
{
  "lambda_name": "my-function",
  "payload_json": {"action": "ping"},
  "invocation_type": "RequestResponse",
  "include_logs": true
}
```

- **Step Functions Start Execution** – Starts a state machine by ARN, passing execution input, optional name, trace header, and tags so agents can fan out or orchestrate long-running jobs.

```json
{
  "state_machine_arn": "arn:aws:states:us-east-1:111122223333:stateMachine:MyFlow",
  "input_json": {"task": "sync"},
  "name": "run-001"
}
```

## Privacy Policy

The plugin is designed to interact with AWS services (such as Bedrock, Lambda, S3, and DynamoDB) on your behalf. It does not collect analytics or telemetry beyond what is required to fulfill the tool invocations you issue.

### Data Collection

- **User-supplied inputs.** Text prompts, speech/audio URLs, translation requests, Lambda payloads, and other parameters that you pass to the tools are sent to the corresponding AWS service only for the purpose of executing that tool invocation.
- **Configuration metadata.** Optional AWS credentials (access key, secret key, region) may be provided either at the provider level or per tool. These values stay within the plugin runtime and are forwarded solely to AWS SDK clients to authenticate requests.
- **Generated outputs.** Responses received from AWS (e.g., Bedrock retrieve results or other tool outputs) are returned directly to Dify and are not stored elsewhere by this plugin.
- The plugin does **not** collect personally identifiable information unless included in the data that you explicitly send to the tools.

### Data Usage

- Inputs are transmitted to AWS services strictly to execute the selected tool (e.g., running Transcribe, retrieving from Bedrock KB, generating Nova images/videos, reranking documents).
- Outputs from AWS are returned to the Dify workflow or agent as-is. No secondary processing or analysis is performed beyond light formatting necessary for the Dify UI.
- The plugin does not sell, share, or reuse your data for any other purpose. Data is not used for model training by this plugin.

### Data Storage

- By default, the plugin does **not** store any user inputs or outputs on its own disk.
- Temporary files (e.g., downloaded GIFs for frame extraction) are written to local storage only for the duration of the request and deleted immediately after completion.
- Any persistent storage happens only when you instruct a tool to do so (e.g., writing a file to S3 or DynamoDB via the respective tools). In such cases the data resides in your AWS account under the resources you control.

### Third-party Services

- The plugin communicates exclusively with AWS services using the official AWS SDK (boto3) and, for browser automation, the Bedrock AgentCore Browser service plus Playwright. No other third-party APIs are contacted.
- When using OpenSearch, SageMaker, Bedrock, Lambda, Transcribe, Comprehend, S3, or DynamoDB tools, the data is transmitted directly to those AWS endpoints over HTTPS.
- Browser tooling stores connection metadata (WebSocket URLs, headers) in AWS Systems Manager Parameter Store in your account so that sessions can be reused. These parameters contain no additional user data beyond what is required to connect.

### Security

- All network calls to AWS services use HTTPS, and AWS credentials are loaded into boto3 clients only when needed. If you provide credentials via the provider settings, they remain in memory within the plugin runtime and are not persisted.
- Parameter Store entries created for AgentCore Browser sessions are stored in your AWS account and inherit the IAM policies you configure.
- The browser tool caches Playwright sessions in memory only for the life of the plugin process and cleans up resources when sessions are closed.
- Temporary files for media processing are stored under the plugin workspace with restrictive permissions and are deleted after each request.
- It is your responsibility to secure your AWS resources (IAM policies, S3 bucket ACLs, DynamoDB tables, etc.). The plugin will operate with whatever permissions the provided credentials allow.
