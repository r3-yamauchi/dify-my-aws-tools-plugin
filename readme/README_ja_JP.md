# my_aws_tools

**Author:** r3-yamauchi  
**Version:** 1.0.7  
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
- Agentcore Code Interpreter
- Agentcore Memory
- Agentcore Memory Search

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

### エージェントコア連携

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
