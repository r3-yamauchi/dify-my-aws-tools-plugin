# my_aws_tools

**Author:** r3-yamauchi
**Version:** 1.0.3  
**Type:** tool

English | [Japanese](https://github.com/r3-yamauchi/dify-my-aws-tools-plugin/blob/main/readme/README_ja_JP.md)

## Description

The source code of this plugin is available in the [GitHub repository](https://github.com/r3-yamauchi/dify-my-aws-tools-plugin).

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-my-aws-tools-plugin)

## Fork Status

This repository is a personal fork of the official LangGenius AWS Tools plugin (release 0.0.15) under the Apache License 2.0.

## Overview

The AWS Tools plugin bundles multiple AWS services so that Dify applications can trigger content moderation, document reranking, text-to-speech, speech recognition, and other workflows directly inside the platform.

Included tools:
- Apply Guardrail
- Bedrock Retrieve
- Bedrock Retrieve and Generate
- Bedrock KB List
- Bedrock KB Data Sources
- Bedrock KB Sync
- Lambda Translate Utils
- Lambda YAML to JSON
- Lambda Invoker
- Step Functions Start Execution
- Nova Canvas
- Nova Reel
- S3 Operator
- S3 File Uploader
- S3 File Download
- S3 List Buckets
- S3 Create Bucket
- S3 List Objects
- SageMaker Chinese Toxicity Detector
- SageMaker Text Rerank
- SageMaker TTS
- Transcribe ASR

## License & Attribution

This project is distributed under the Apache License 2.0. See `LICENSE` for the full text and `NOTICE` for attribution requirements, which also document that this implementation derives from the LangGenius official plugin sources.

## Feature Highlights by Category

### Amazon Bedrock
- **Bedrock Retrieve** – Calls the `bedrock-agent-runtime` Retrieve API to run semantic or hybrid searches against a selected Knowledge Base. You can switch metadata filters, result counts, and Bedrock Reranking models (cohere.rerank-v3-5 / amazon.rerank-v1), and receive outputs as JSON or ranked text.
- **Bedrock Retrieve and Generate** – Wraps `retrieve_and_generate` so KNOWLEDGE_BASE or EXTERNAL_SOURCES flows run in a single call. Supplying `session_configuration` and `session_id` lets Bedrock maintain session state, and the tool returns the text plus citation metadata.
- **Apply Guardrail** – Uses Bedrock Runtime `apply_guardrail` with a guardrail ID/version, source, and text to evaluate safety, returning the action, generated output, and per-policy violations.
- **Nova Canvas** – Invokes Nova Canvas v1 for TEXT_IMAGE, COLOR_GUIDED, IMAGE_VARIATION, INPAINTING, OUTPAINTING, and BACKGROUND_REMOVAL tasks. Input images are fetched from S3 and outputs are uploaded back while also streamed to Dify as PNG blobs.
- **Nova Reel** – Uses Nova Reel v1 to create videos from text or from a seed image. Results are saved as MP4 files in the specified S3 path, and synchronous mode polls until completion to return the binary.

### Audio & Media Processing
- **Transcribe ASR** – Downloads audio via HTTP/S, uploads it to S3, and launches `start_transcription_job`. Supports LanguageCode, IdentifyLanguage, IdentifyMultipleLanguages, and speaker diarization, yielding plain text or speaker-tagged transcripts.
- **SageMaker TTS** – Sends requests to a SageMaker Runtime endpoint in Preset Voice, Clone Voice, Clone Voice Cross Lingual, or Instruct Voice mode. Cross-lingual inference detects the language with Amazon Comprehend and returns an audio file via S3 presigned URL.
- **Extract Frame** – Downloads GIF animations and extracts evenly spaced PNG frames. Users choose the number of frames (from two for first/last to any higher count), and each frame is returned as binary output.

### Language & Translation Utilities
- **Lambda Translate Utils** – Posts JSON payloads (source/destination languages, dictionary ID, model ID, request type, text) to a user-managed Lambda function, which is expected to perform translation or terminology mapping.
- **Lambda YAML to JSON** – Calls a Lambda function synchronously with YAML text in the request body and returns the JSON body only when the Lambda responds with `statusCode` 200.
- **Translation Evaluator** – Tokenizes Chinese text via `jieba` and computes sacrebleu, METEOR, and NIST scores. Provide `label` (reference) and `translation` (hypothesis), with an optional hook to a SageMaker endpoint for additional evaluation.
- **SageMaker Chinese Toxicity Detector** – Sends Chinese text to a SageMaker endpoint and normalizes the prediction to SAFE or NO_SAFE, handling both direct and `body.prediction` response formats.

### Data Search & RAG Support
- **Bedrock KB List** – Calls `list_knowledge_bases` to enumerate available knowledge bases, returning summaries (status, creation date, vector store) and pagination tokens for downstream filtering.
- **Bedrock KB Data Sources** – Invokes `list_data_sources` for a given knowledge base, returning connector information, synchronization state, and pagination tokens so you can select the correct source before running ingestion jobs.
- **Bedrock KB Sync** – Calls `StartIngestionJob` for a given knowledge base/data source pair so you can synchronize documents on demand, optionally setting a client token or deletion policy.
- **OpenSearch kNN Search** – Generates embeddings for text or S3-hosted images via Bedrock and queries Amazon OpenSearch (Serverless/Managed) using kNN, returning only the selected metadata fields plus scores.
- **SageMaker Text Rerank** – Reranks JSON candidates through a SageMaker endpoint, appends `score` to each item, and returns the top K entries for RAG pipelines.

### Storage & Database Operations
- **S3 Operator** – Reads or writes text content to `s3://` URIs and optionally produces presigned URLs. `write` uploads UTF-8 text; `read` returns either the text body or a presigned link.
- **S3 File Uploader** – Accepts a file emitted by an upstream workflow node, uploads it to the specified bucket/key prefix, and can optionally return a presigned URL so later nodes can fetch the object without AWS credentials.
- **S3 File Download** – Fetches objects from S3; either returns a presigned URL or streams the binary into the workflow along with a variable containing bucket/key metadata for downstream nodes.
- **DynamoDB Manager** – Offers PAY_PER_REQUEST table creation plus `put_item`, `get_item`, and `delete_item`, supporting custom partition/sort keys and JSON `item_data` payloads.

### AgentCore Integrations
- **AgentCore Memory** – Creates memory resources via the AgentCore SDK, records conversations when `operation=record`, and fetches history with `get_last_k_turns` when `operation=retrieve`. Missing IDs are created automatically and returned as JSON.
- **AgentCore Memory Search** – Executes `retrieve_memories` for a given memory ID/namespace, limits the results to the requested top_k, and serializes timestamps to ISO 8601.
- **Agentcore Browser Session Manager** – Starts/stops browser sessions via BrowserClient and stores CDP WebSocket data in Parameter Store (`/browser-session/<session_id>`), deleting it when the session closes.
- **Agentcore Browser Tool** – Initializes Playwright from Parameter Store and supports `browse_url`, `search_web`, `extract_content`, `fill_form`, and `execute_script`, returning structured JSON for each action.
- **Agentcore Code Interpreter** – Launches Bedrock AgentCore Code Interpreter sessions, optionally creates the interpreter, executes shell commands (`command`) and language-specific code (`language` + `code`), and returns IDs with the results.

### Other Notes
- **Lambda Translate Utils / Lambda YAML to JSON** – Lightweight wrappers for reusing your Lambda workloads from workflows.
- **Lambda Invoker** – Calls any Lambda function name or ARN with a JSON payload, optional qualifier, per-call credentials, and tail logs for quick serverless utilities.
- **Step Functions Start Execution** – Starts a state machine by ARN, passing execution input, optional name, trace header, and tags so agents can fan out or orchestrate long-running jobs.
- **Transcribe ASR / Nova Canvas / Nova Reel** – Provide the audio, image, and video pipelines described above for immediate invocation as Dify tools.

## Privacy Policy

The plugin is designed to interact with AWS services (such as Bedrock, SageMaker, Lambda, Transcribe, OpenSearch, S3, and DynamoDB) on your behalf. It does not collect analytics or telemetry beyond what is required to fulfill the tool invocations you issue.

### Data Collection
- **User-supplied inputs.** Text prompts, speech/audio URLs, translation requests, Lambda payloads, and other parameters that you pass to the tools are sent to the corresponding AWS service only for the purpose of executing that tool invocation.
- **Configuration metadata.** Optional AWS credentials (access key, secret key, region) may be provided either at the provider level or per tool. These values stay within the plugin runtime and are forwarded solely to AWS SDK clients to authenticate requests.
- **Generated outputs.** Responses received from AWS (e.g., transcription text, Bedrock retrieve results, SageMaker inference outputs, browser-session information) are returned directly to Dify and are not stored elsewhere by this plugin.
- The plugin does **not** collect personally identifiable information unless included in the data that you explicitly send to the tools.

### Data Usage
- Inputs are transmitted to AWS services strictly to execute the selected tool (e.g., running Transcribe, retrieving from Bedrock KB, generating Nova images/videos, reranking documents).
- Outputs from AWS are returned to the Dify workflow or agent as-is. No secondary processing or analysis is performed beyond light formatting necessary for the Dify UI.
- The plugin does not sell, share, or reuse your data for any other purpose. Data is not used for model training by this plugin.

### Data Storage
- By default, the plugin does **not** store any user inputs or outputs on its own disk.
- Temporary files (e.g., downloaded GIFs for frame extraction) are written to local storage only for the duration of the request and deleted immediately after completion.
- Any persistent storage happens only when you instruct a tool to do so (e.g., writing a file to S3 or DynamoDB via the respective tools). In such cases the data resides in your AWS account under the resources you control.
- **Exception – AgentCore Browser Session Manager.** To reopen Playwright browser sessions without re-authenticating, this tool writes minimal session metadata (session ID, WebSocket endpoint, serialized headers/tokens required by AgentCore) to AWS Systems Manager Parameter Store in *your* AWS account. These parameters never leave your account, contain no page content or prompts, and are deleted automatically when you call `close_browser_session` or when the stored TTL expires. If you prefer not to retain this metadata, disable the browser session manager or periodically purge the corresponding Parameter Store path.

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
