# my_aws_tools

**Author:** r3-yamauchi  
**Version:** 1.0.10  
**Type:** tool

英語版ドキュメントはリポジトリ直下の `README.md` を参照してください。

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-my-aws-tools-plugin)

## フォーク状況

本リポジトリは [AWS Tools プラグイン](https://github.com/langgenius/dify-official-plugins/tree/main/tools/aws) (リリース 0.0.15) を Apache License 2.0 の条件でフォークした個人プロジェクトです。

## 概要

このツール・プラグインは、いくつかの AWS サービスに基づくツールセットを提供し、Dify アプリケーションの中で AWS の機能を直接活用できるようにします。
オリジナルの [AWS Tools プラグイン](https://github.com/langgenius/dify-official-plugins/tree/main/tools/aws) には含まれていない独自ツールを追加し、（利用頻度が低く、私が保守していくことは難しいと感じた）いくつかのツールを削除しました。また、各ツールに独自のパラメータと日本語訳を追加しています。

含まれるツール:

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
- CloudWatch Logs Describe Streams
- CloudWatch Logs Filter Events
- CloudWatch Logs Get Events
- CloudWatch Logs Insight
- Agentcore Code Interpreter
- Agentcore Code Interpreter Files
- Agentcore Memory
- Agentcore Memory Search
- Agentcore Memory Search Advanced
- Agentcore Memory Backup
- Agentcore Memory Merge/Split
- Agentcore Memory Manager
- Agentcore Memory Query
- Agentcore Memory Statistics
- Agentcore Memory Template
- Agentcore Event Manager
- Agentcore Runtime
- Agentcore Observability
- Get Credentials
- STS AssumeRole

## ライセンスとクレジット

本プロジェクトは Apache License 2.0 の下で配布されています。全文は `LICENSE` を確認し、派生物を再配布する際のクレジット要件は `NOTICE` を参照してください。 `NOTICE` には、この実装が https://github.com/langgenius/dify-official-plugins/tree/main/tools/aws を由来としていることを明記しています。

## ツール別機能概要

### Amazon Bedrock 系

- **Bedrock Retrieve**: `bedrock-agent-runtime` の Retrieve API を直接呼び出し、指定した Knowledge Base に対してセマンティックまたは HYBRID 検索を実行します。

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "query": "最新のプロダクトロードマップ",
  "search_type": "HYBRID",
  "max_results": 5,
  "guardrail_id": "ab1cd2e3f45g"
}
```

- **Bedrock Retrieve and Generate**: `retrieve_and_generate` を呼び出します。knowledge_base_configuration または external_sources_configuration を JSON で渡すと、検索と生成を一括実行します。session_configuration と session_id を指定すれば Bedrock 側に会話状態を保持できます。

```json
{
  "result_type": "JSON",
  "input": "インシデント対応手順を要約してください",
  "type": "ナレッジベース",
  "knowledge_base_configuration": {
    "knowledgeBaseId": "ABCDEFG8H9",
    "modelArn":"arn:aws:bedrock:us-west-2::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "retrievalConfiguration": {
      "vectorSearchConfiguration": {"numberOfResults": 3}
    }
  }
}
```

- **Apply Guardrail**: `apply_guardrail` を呼び出します。

#### Apply Guardrail サンプルリクエスト (複数テキスト)

```json
{
  "guardrail_id": "ab1cd2e3f45g",
  "guardrail_version": "1",
  "source": "入力",
  "content": [
    { "text": { "text": "ユーザーの入力テキスト1" } },
    { "text": { "text": "ユーザーの入力テキスト2" } }
  ]
}
```

#### Apply Guardrail サンプルリクエスト (画像 + テキスト)

```json
{
  "guardrail_id": "ab1cd2e3f45g",
  "guardrail_version": "2",
  "source": "出力",
  "content": [
    {
      "image": {
        "format": "png",
        "source": { "s3Uri": "s3://bucket/path/image.png" }
      }
    },
    { "text": { "text": "LLM が生成した応答" } }
  ]
}
```

- **Nova Canvas**: Bedrock Nova Canvas v1 を用いた画像生成ツールで、TEXT_IMAGE・COLOR_GUIDED・IMAGE_VARIATION・INPAINTING・OUTPAINTING・BACKGROUND_REMOVAL を選択できます。入力画像が必要なタスクでは S3 からバイナリを取得し、出力は S3 へ PNG 保存すると同時に Dify へバイナリを返送します。

```json
{
  "task": "TEXT_IMAGE",
  "prompt": "嵐の中の灯台",
  "output_s3_uri": "s3://my-bucket/outputs/canvas.png"
}
```

- **Nova Reel**: Bedrock Nova Reel v1 の非同期 API を利用してテキスト→動画、または画像を初期フレームにした動画生成を行います。指定 S3 パスへ MP4 を出力し、同期モードでは完了をポーリングして動画バイナリも返します。

```json
{
  "mode": "TEXT_TO_VIDEO",
  "prompt": "雪山をドローンが飛ぶ映像",
  "output_s3_uri": "s3://my-bucket/outputs/reel.mp4",
  "wait_for_completion": true
}
```

### 音声・メディア処理

- **Extract Frame**: GIF アニメーションの URL をダウンロードし、総フレーム数に応じて均等間隔の PNG フレームを抽出します。抽出枚数は 2 枚（先頭・末尾）から任意の回数まで指定でき、各フレームをバイナリで返却します。

```json
{
  "gif_url": "https://example.com/anim.gif",
  "frame_count": 4
}
```

- **Lambda YAML to JSON**: YAML テキストを `body` に入れて Lambda を同期呼び出しし、statusCode 200 のときのみ JSON 文字列を返します。YAML→JSON 変換をサーバーレスで統一できます。

```yaml
lambda_name: yaml-to-json
yaml_content: |
  key: value
  list:
    - a
    - b
```

- **Bedrock KB List**: `list_knowledge_bases` API を呼び出してナレッジベースサマリーを取得し、ステータスや作成日時、ベクトルストア設定、nextToken を返します。

```json
{
  "max_results": 20
}
```

- **Bedrock KB Data Sources**: `list_data_sources` で指定 knowledgeBaseId の接続データソースを列挙し、同期状態・コネクター種別・nextToken を返すため、後続の同期ジョブ選択が容易になります。

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "max_results": 10
}
```

- **Bedrock KB Sync**: knowledgeBaseId と dataSourceId を渡して `StartIngestionJob` を呼び出し、必要に応じて clientToken や dataDeletionPolicy を指定しながらオンデマンド同期を開始します。

```json
{
  "knowledge_base_id": "ABCDEFG8H9",
  "data_source_id": "ds-001",
  "client_token": "sync-20250227"
}
```

### ストレージ／データベース操作

- **CloudFront Create Invalidation**: CloudFront ディストリビューションに対して `create_invalidation` を送信します。`paths` または `invalidation_batch` を受け付け、`caller_reference` 未指定時は自動生成します。

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

- **S3 File Uploader**: ワークフローから受け取ったファイルを指定のバケット/キーへアップロードし、必要に応じてプリサイン URL を返します。

```json
{
  "bucket_name": "my-bucket",
  "object_key": "uploads/example.txt",
  "file": "{{file}}",
  "return_presigned_url": true
}
```

- **S3 Operator (write)**: `s3://` URI を解析しテキストを書き込む例。

```json
{
  "operation": "write",
  "s3_uri": "s3://my-bucket/config.json",
  "text": "{\"env\":\"prod\"}"
}
```

- **S3 File Download**: S3 からオブジェクトを取得し、プリサイン URL を返すかバイナリを直接返却します（`presign_only` などで指定）。

```json
{
  "bucket_name": "my-bucket",
  "object_key": "reports/latest.pdf",
  "presign_only": true,
  "expires_in": 600
}
```

- **DynamoDB Manager**: PAY_PER_REQUEST でのテーブル作成や `put_item`/`get_item`/`delete_item` を 1 つのツールで提供します。

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

### CloudWatch Logs 系

- **CloudWatch Logs Describe Streams**: 指定した CloudWatch Logs ロググループ内のログストリームを検索・一覧取得します。プレフィックスフィルタリングやソート順の指定が可能で、後続のログイベント検索処理を効率化します。

```json
{
  "log_group_name": "/aws/lambda/my-function",
  "log_stream_name_prefix": "2025/01/04",
  "order_by": "LastEventTime",
  "descending": true,
  "max_items": 20
}
```

- **CloudWatch Logs Filter Events**: CloudWatch Logs から時間範囲とフィルターパターンを使用してログイベントを検索・取得します。複数のログストリームを対象にした横断検索や、特定の文字列パターンでの絞り込みが可能です。

```json
{
  "log_group_name": "/aws/lambda/my-function",
  "log_stream_names": "2025/01/04/[$LATEST]abc123,2025/01/04/[$LATEST]def456",
  "start_time": "1h",
  "filter_pattern": "ERROR",
  "max_events": 500
}
```

- **CloudWatch Logs Get Events**: 指定した CloudWatch Logs ログストリームから全ログイベントを取得します。双方向ページネーションに対応し、大量のログデータを効率的に処理できます。

```json
{
  "log_group_name": "/aws/lambda/my-function",
  "log_stream_name": "2025/01/04/[$LATEST]abc123",
  "start_time": "2025-01-04T00:00:00Z",
  "end_time": "2025-01-04T23:59:59Z",
  "start_from_head": true,
  "max_events": 10000
}
```

- **CloudWatch Logs Insight**: CloudWatch Logs Insight の強力なクエリ言語を使用して高度なログ分析、集計、可視化を実行します。複数のロググループを対象にした横断分析や統計処理が可能です。

```json
{
  "log_group_names": "/aws/lambda/function1,/aws/lambda/function2",
  "query_string": "fields @timestamp, @message | filter @message like /ERROR/ | stats count() by bin(5m) | sort @timestamp desc",
  "start_time": "1d",
  "max_results": 1000
}
```

### メッセージング

- **SNS Publish**: SNS トピック ARN へメッセージを公開します。件名や MessageAttributes を付与可能。

```json
{
  "topic_arn": "arn:aws:sns:us-east-1:111122223333:alerts",
  "message": "Deployed v1.2.3",
  "subject": "Deploy notice"
}
```

- **SQS Send Message**: SQS キュー URL へメッセージを送信します。遅延秒数や MessageAttributes を指定可能。

```json
{
  "queue_url": "https://sqs.us-east-1.amazonaws.com/111122223333/tasks",
  "message_body": "{\"job_id\":123}",
  "delay_seconds": 5
}
```

### Amazon Bedrock AgentCore連携

- **AgentCore Runtime**: Runtime エージェントの起動、呼び出し、ステータス確認を行います。同期・非同期の両方の呼び出しモードをサポートし、セッション管理や実行結果の取得が可能です。

```json
{
  "operation": "invoke",
  "agent_id": "agent-abc123",
  "input_text": "データ分析を実行してください",
  "session_id": "session-001",
  "enable_trace": true,
  "end_session": false
}
```

- **AgentCore Memory Manager**: Memory リソースのライフサイクル管理を行います。Memory の一覧取得、詳細情報の取得、作成、削除が可能で、フィルタリングやソート機能も提供します。

```json
{
  "operation": "list",
  "max_results": 50,
  "filter_name_prefix": "prod-",
  "sort_by": "createdAt",
  "sort_order": "desc"
}
```

```json
{
  "operation": "create",
  "memory_name": "customer-support-memory",
  "description": "カスタマーサポート用のメモリー",
  "tags": {
    "Environment": "production",
    "Team": "support"
  }
}
```

- **AgentCore Event Manager**: Memory イベントの詳細管理を行います。イベントの一覧取得、詳細取得、削除（個別・バッチ）、エクスポート（JSON、CSV）が可能で、時間範囲や Actor ID、Session ID でのフィルタリングをサポートします。

```json
{
  "operation": "list",
  "memory_id": "mem-abc123",
  "start_time": "2025-01-01T00:00:00Z",
  "end_time": "2025-01-31T23:59:59Z",
  "filter_actor_id": "user001",
  "max_results": 100
}
```

```json
{
  "operation": "export",
  "memory_id": "mem-abc123",
  "format": "csv",
  "output_location": "s3://my-bucket/exports/events.csv",
  "start_time": "1w"
}
```

- **AgentCore Memory Statistics**: Memory の使用状況を分析します。Memory リソースの統計情報、Actor 別のイベント数集計、Session 別のイベント数集計、時系列でのイベント数推移を取得できます。

```json
{
  "operation": "memory_stats",
  "memory_id": "mem-abc123"
}
```

```json
{
  "operation": "actor_stats",
  "memory_id": "mem-abc123",
  "start_time": "7d",
  "top_n": 10
}
```

```json
{
  "operation": "timeline",
  "memory_id": "mem-abc123",
  "start_time": "2025-01-01T00:00:00Z",
  "end_time": "2025-01-31T23:59:59Z",
  "interval": "1h"
}
```

- **AgentCore Observability**: AgentCore Observability のデータを統合的に取得します。セッション・トレース・スパンのメトリクス、CloudWatch Logs からのログ取得、X-Ray トレースデータの取得、パフォーマンス分析とボトルネック特定が可能です。

```json
{
  "operation": "get_session_metrics",
  "session_id": "session-abc123",
  "metric_types": "duration,token_count,error_rate"
}
```

```json
{
  "operation": "get_logs",
  "log_group_name": "/aws/bedrock/agentcore",
  "start_time": "1h",
  "filter_pattern": "ERROR",
  "max_events": 100
}
```

```json
{
  "operation": "analyze_performance",
  "trace_id": "trace-abc123",
  "include_bottlenecks": true
}
```

- **AgentCore Memory Backup**: Memory リソースの完全なバックアップ・リストア機能を提供します。ディザスタリカバリや環境間のデータ移行に使用できます。バックアップは S3 に保存され、オプションで圧縮・暗号化が可能です。

```json
{
  "operation": "backup",
  "memory_id": "mem-abc123",
  "backup_location": "s3://my-backup-bucket/backups/",
  "include_events": true,
  "include_strategies": true,
  "compression": "gzip",
  "encryption": true
}
```

```json
{
  "operation": "restore",
  "backup_location": "s3://my-backup-bucket/backups/mem-abc123-20250115.json.gz",
  "target_memory_id": "mem-new123",
  "conflict_resolution": "skip"
}
```

- **AgentCore Memory Merge/Split**: Memory リソースのマージ・分割・イベントコピー機能を提供します。複数の Memory を統合したり、1つの Memory を Actor や Session ごとに分割したり、特定の条件に一致するイベントのみをコピーできます。

```json
{
  "operation": "merge",
  "source_memory_ids": "mem-abc123,mem-def456,mem-ghi789",
  "target_memory_id": "mem-merged",
  "merge_conflict_resolution": "keep_latest",
  "deduplicate": true,
  "merge_strategies": true
}
```

```json
{
  "operation": "split",
  "source_memory_id": "mem-abc123",
  "split_by": "actor_id",
  "target_memory_prefix": "memory-actor-",
  "create_index": true
}
```

- **AgentCore Memory Query**: 複雑な検索条件を構築して Memory を検索します。複数条件の組み合わせ（AND/OR/NOT）、正規表現検索、類似度検索（ベクトル検索）、クエリの保存と再利用が可能です。

```json
{
  "operation": "search",
  "memory_id": "mem-abc123",
  "query": {
    "and": [
      {"field": "actor_id", "operator": "equals", "value": "user001"},
      {"field": "timestamp", "operator": "greater_than", "value": "2025-01-01T00:00:00Z"}
    ]
  },
  "max_results": 50
}
```

```json
{
  "operation": "similarity_search",
  "memory_id": "mem-abc123",
  "query_text": "エラーの原因を調査",
  "top_k": 10,
  "similarity_threshold": 0.7
}
```

- **AgentCore Memory Template**: Memory 設定のテンプレート化を行います。Memory 設定テンプレートの作成、テンプレートからの Memory 作成、テンプレートの共有とインポート、バージョン管理が可能です。

```json
{
  "operation": "create_template",
  "template_name": "customer-support-template",
  "description": "カスタマーサポート用の標準テンプレート",
  "memory_config": {
    "retention_days": 90,
    "max_events": 10000,
    "enable_search": true
  },
  "tags": {
    "Type": "support",
    "Version": "1.0"
  }
}
```

```json
{
  "operation": "create_from_template",
  "template_id": "template-abc123",
  "memory_name": "support-team-a-memory",
  "override_config": {
    "retention_days": 180
  }
}
```

- **AgentCore Code Interpreter Files**: Code Interpreter のファイル管理を行います。ファイルアップロード（ローカル/Base64）、実行結果のファイル取得、ファイル一覧取得（フィルター、ソート）、ファイル削除（単一/一括）が可能で、ファイルサイズ制限（100MB）とセキュリティチェックを実装しています。

```json
{
  "operation": "upload",
  "session_id": "session-abc123",
  "file_path": "/path/to/data.csv",
  "file_name": "data.csv",
  "description": "分析用データ"
}
```

```json
{
  "operation": "list",
  "session_id": "session-abc123",
  "filter_extension": ".csv",
  "sort_by": "upload_time",
  "sort_order": "desc"
}
```

- **AgentCore Memory Search Advanced**: Memory Search の機能を拡張します。フィルター条件の追加（時間範囲、Actor ID、Session ID、Namespace）、ソート順の指定（関連度、タイムスタンプ）、ページネーション（最大100件/ページ）、ハイライト機能（カスタマイズ可能）、コンテキスト抽出が可能です。

```json
{
  "memory_id": "mem-abc123",
  "query": "エラー処理",
  "filter_actor_id": "user001",
  "filter_start_time": "2025-01-01T00:00:00Z",
  "filter_end_time": "2025-01-31T23:59:59Z",
  "sort_by": "relevance",
  "page_size": 50,
  "enable_highlight": true,
  "context_size": 100
}
```

- **Agentcore Code Interpreter**: Code Interpreter セッションを作成/利用してコマンドやコードを実行します。

```json
{
  "operation": "execute",
  "code": "print(1+1)",
  "language": "python"
}
```

- **AgentCore Memory Search**: 指定 memory/namespace に対して `retrieve_memories` を実行し、top_k で件数制限します。

```json
{
  "memory_id": "mem-abc",
  "namespace": "default",
  "query": "error logs",
  "top_k": 5
}
```

- **AgentCore Memory**: Memory リソースの作成・記録・取得を行います（`operation=record` または `retrieve` を指定）。

```json
{
  "operation": "record",
  "memory_id": "mem-123",
  "actor_id": "user",
  "role": "user",
  "content": "こんにちは"
}
```

### そのほか

- **Lambda Invoker**: FunctionName/ARN、JSON ペイロード、Qualifier、InvocationType（RequestResponse/Event/DryRun）を指定して任意の Lambda を実行します。Tail ログを含める設定を有効にすると、最大 4 KB の実行ログを結果 JSON に同梱します。

```json
{
  "lambda_name": "my-function",
  "payload_json": {"action": "ping"},
  "invocation_type": "RequestResponse",
  "include_logs": true
}
```

- **Step Functions Start Execution**: ステートマシン ARN と入力 JSON、必要に応じて execution name／trace header／タグを渡して `start_execution` を呼び出します。戻り値には executionArn・開始時刻が含まれ、後続ノードでポーリングやモニタリングに利用できます。

```json
{
  "state_machine_arn": "arn:aws:states:us-east-1:111122223333:stateMachine:MyFlow",
  "input_json": {"task": "sync"},
  "name": "run-001"
}
```

- **Get Credentials**: boto3.Session から AWS 認証情報を取得します。指定したプロファイルとリージョンを使用して、アクセスキー、シークレットキー、セッショントークンを JSON 形式で返却します。`profile_name` や `region_name` を指定しない場合は、AWS Credential Provider Chain（環境変数、~/.aws/credentials、IAM ロールなど）を使用してデフォルトの認証情報を取得します。EC2 インスタンスや ECS タスクで実行する場合は、インスタンスプロファイルやタスクロールから一時的な認証情報を自動的に取得できます。

```json
{
  "profile_name": "development",
  "region_name": "ap-northeast-1"
}
```

```json
{}
```
（パラメータなしでデフォルト認証情報を取得）

- **STS AssumeRole**: AWS STS を使用して IAM ロールを引き受け、一時的な認証情報を取得します。クロスアカウントアクセスや権限昇格が必要な場合に使用します。MFA 認証、外部 ID、セッションポリシーなどの高度な設定にも対応しています。取得した認証情報は Get Credentials ツールと互換性のある形式で返却されます。

```json
{
  "role_arn": "arn:aws:iam::123456789012:role/MyRole",
  "role_session_name": "DifySession",
  "duration_seconds": 3600
}
```

```json
{
  "role_arn": "arn:aws:iam::123456789012:role/CrossAccountRole",
  "role_session_name": "CrossAccountSession",
  "external_id": "unique-external-id-123",
  "serial_number": "arn:aws:iam::123456789012:mfa/user",
  "token_code": "123456"
}
```
