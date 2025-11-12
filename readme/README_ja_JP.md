# my_aws_tools

**Author:** r3-yamauchi  
**Version:** 1.0.2  
**Type:** tool

英語版ドキュメントはリポジトリ直下の `README.md` を参照してください。

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/r3-yamauchi/dify-my-aws-tools-plugin)

## フォーク状況

本リポジトリは、LangGenius 公式 AWS Tools プラグイン (リリース 0.0.15) を Apache License 2.0 の条件でフォークした個人プロジェクトです。

## 概要

AWS Tools プラグインは、複数の AWS サービスに基づくツールセットを提供し、Dify アプリケーションの内部から AWS の機能を直接活用できるようにします。コンテンツモデレーション、テキストのリランク、テキスト読み上げ、音声認識など、幅広い機能領域をカバーします。

含まれるツール:
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
- SageMaker Chinese Toxicity Detector
- SageMaker Text Rerank
- SageMaker TTS
- Transcribe ASR

## ライセンスとクレジット

本プロジェクトは Apache License 2.0 の下で配布されています。全文は `LICENSE` を確認し、派生物を再配布する際のクレジット要件は `NOTICE` を参照してください。NOTICE には、この実装が LangGenius 公式プラグインを由来としていることを明記しています。

## ツール別機能概要

### Amazon Bedrock 系
- **Bedrock Retrieve**: `bedrock-agent-runtime` の Retrieve API を直接呼び出し、指定 Knowledge Base に対してセマンティックまたは HYBRID 検索を実行します。メタデータフィルタ、検索件数、Bedrock Reranking (cohere.rerank-v3-5 や amazon.rerank-v1) を切り替えられ、結果は JSON あるいは順位付きテキストで取得できます。
- **Bedrock Retrieve and Generate**: Bedrock の `retrieve_and_generate` をラップし、KNOWLEDGE_BASE/EXTERNAL_SOURCES 構成を JSON で渡して検索＋生成を一括実行します。session_configuration と session_id を指定すれば Bedrock 側に会話状態を保持でき、引用情報付きで JSON／テキストを返します。
- **Apply Guardrail**: Bedrock Runtime の `apply_guardrail` を呼び出し、Guardrail ID/Version・source・text を渡してコンテンツ安全性を評価します。レスポンスには action、生成出力、ポリシーごとの違反トピックが含まれます。
- **Nova Canvas**: Bedrock Nova Canvas v1 を用いた画像生成ツールで、TEXT_IMAGE・COLOR_GUIDED・IMAGE_VARIATION・INPAINTING・OUTPAINTING・BACKGROUND_REMOVAL を選択できます。入力画像が必要なタスクでは S3 からバイナリを取得し、出力は S3 へ PNG 保存すると同時に Dify へバイナリを返送します。
- **Nova Reel**: Bedrock Nova Reel v1 の非同期 API を利用してテキスト→動画、または画像を初期フレームにした動画生成を行います。指定 S3 パスへ MP4 を出力し、同期モードでは完了をポーリングして動画バイナリも返します。

### 音声・メディア処理
- **Transcribe ASR**: HTTP/S から音声をダウンロードして S3 に保存する補助を備え、Amazon Transcribe の `start_transcription_job` を起動します。LanguageCode/IdentifyLanguage/IdentifyMultipleLanguages の排他制御やスピーカーダイアライゼーションをサポートし、トランスクリプト JSON からテキストまたは話者付き書き起こしを作成します。
- **SageMaker TTS**: SageMaker Runtime エンドポイントへ 4 種の推論モード（Preset Voice、Clone Voice、Clone Voice Cross Lingual、Instruct Voice）を送信します。クロスリンガル時は Amazon Comprehend で言語タグを推定し、結果の音声は S3 プリサイン URL として返却されます。
- **Extract Frame**: GIF アニメーションの URL をダウンロードし、総フレーム数に応じて均等間隔の PNG フレームを抽出します。抽出枚数は 2 枚（先頭・末尾）から任意の回数まで指定でき、各フレームをバイナリで返却します。

### 言語・翻訳ユーティリティ
- **Lambda Translate Utils**: 任意の Lambda 関数にソース/ターゲット言語、辞書 ID、モデル ID、request_type、テキストを JSON で渡し、翻訳結果文字列を受け取ります。Lambda 側にカスタム辞書や Bedrock モデル呼び出しを実装する前提です。
- **Lambda YAML to JSON**: YAML テキストを `body` に入れて Lambda を同期呼び出しし、statusCode 200 のときのみ JSON 文字列を返します。YAML→JSON 変換をサーバーレスで統一できます。
- **Translation Evaluator**: `jieba` で中国語テキストを分かち書きし、sacrebleu/METEOR/NIST スコアを算出します。参照訳 (`label`) と生成訳 (`translation`) を渡すだけで評価指標を JSON で返し、SageMaker エンドポイントを追加で呼び出す拡張フックも備えています。
- **SageMaker Chinese Toxicity Detector**: 中国語テキストを SageMaker エンドポイントに送信し、SAFE/NO_SAFE を返します。ネストされた `body.prediction` 形式にも対応して単一ラベルに正規化します。

### データ検索・RAG 補助
- **Bedrock KB List**: `list_knowledge_bases` API を呼び出してナレッジベースサマリーを取得し、ステータスや作成日時、ベクトルストア設定、nextToken を返します。
- **Bedrock KB Data Sources**: `list_data_sources` で指定 knowledgeBaseId の接続データソースを列挙し、同期状態・コネクター種別・nextToken を返すため、後続の同期ジョブ選択が容易になります。
- **Bedrock KB Sync**: knowledgeBaseId と dataSourceId を渡して `StartIngestionJob` を呼び出し、必要に応じて clientToken や dataDeletionPolicy を指定しながらオンデマンド同期を開始します。
- **OpenSearch kNN Search**: Bedrock の埋め込みモデルでテキストと任意の S3 画像をベクトル化し、Amazon OpenSearch (Serverless/Managed) の kNN クエリで上位ドキュメントを検索します。取得した `_source` から指定メタデータフィールドのみを抽出し、スコア付き JSON を返します。
- **SageMaker Text Rerank**: 既存の候補 (`candidate_texts` の JSON) を SageMaker エンドポイントで再スコアリングし、score フィールドを付与したうえでトップ K を返します。RAG パイプラインの再ランキング段に組み込めます。

### ストレージ／データベース操作
- **S3 Operator**: `s3://` URI を解析してバケット/キーを特定し、テキスト読み書きとプリサイン URL の生成を行います。`write` モードでは UTF-8 テキストをアップロードし、`read` モードでは本文または署名付き URL を返します。
- **DynamoDB Manager**: PAY_PER_REQUEST モードでのテーブル作成、`put_item`、`get_item`、`delete_item` を 1 つのツールで提供します。パーティションキー/ソートキー名を個別に指定でき、JSON 文字列の item_data を dict に変換して登録します。

### エージェントコア連携
- **AgentCore Memory**: Bedrock AgentCore SDK で Memory リソースを自動作成し、`operation=record` で会話イベントを保存、`operation=retrieve` で `get_last_k_turns` を実行します。不足している memory_id・actor_id・session_id は作成して JSON 返却します。
- **AgentCore Memory Search**: 既存 Memory ID と namespace を指定し、`retrieve_memories` API でベクトル検索します。最大取得件数や検索クエリはフォームで設定し、結果を ISO8601 化した JSON に整形します。
- **Agentcore Browser Session Manager**: BrowserClient を使ってブラウザセッションを開始/終了し、CDP WebSocket URL や Live View URL を Parameter Store `/browser-session/<session_id>` に書き込みます。クローズ時は SSM パラメータも削除します。
- **Agentcore Browser Tool**: Parameter Store から取得した接続情報で Playwright を初期化し、`browse_url`、`search_web`、`extract_content`、`fill_form`、`execute_script` 等の操作を JSON で返します。ブラウザセッションの再利用やコンテンツ抽出 (見出し/リンク/画像/本文) に対応します。
- **Agentcore Code Interpreter**: Bedrock AgentCore Code Interpreter を起動し、code_interpreter_id や session_id が無ければ自動生成します。Shell コマンド (`command`) とサポート言語のコード (`language`＋`code`) を順番に実行し、結果や ID を JSON で返します。

### そのほか
- **Lambda Invoker**: FunctionName/ARN、JSON ペイロード、Qualifier、InvocationType（RequestResponse/Event/DryRun）を指定して任意の Lambda を実行します。Tail ログを含める設定を有効にすると、最大 4 KB の実行ログを結果 JSON に同梱します。
- **Step Functions Start Execution**: ステートマシン ARN と入力 JSON、必要に応じて execution name／trace header／タグを渡して `start_execution` を呼び出します。戻り値には executionArn・開始時刻が含まれ、後続ノードでポーリングやモニタリングに利用できます。
- **Lambda Translate Utils／Lambda YAML to JSON**: ワークフローから任意の Lambda ワークロードを安全に再利用するための薄いラッパーです。
- **Transcribe ASR／Nova Canvas／Nova Reel など**: 上記の通り、音声・画像・動画のバッチ処理を Dify ツールとして即座に呼び出せます。
