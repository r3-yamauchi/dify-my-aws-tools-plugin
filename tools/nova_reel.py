"""
場所: tools/nova_reel.py
内容: AWS Bedrock Nova Reel モデルを呼び出してテキスト/画像から動画を生成するツール。
目的: Dify のワークフローから非同期/同期モードで動画生成を実行し、S3 へ成果物を保存する。
"""

import base64
import logging
import time
from io import BytesIO
from typing import Any, Optional, Union
from urllib.parse import urlparse
from collections.abc import Generator

import boto3
from botocore.exceptions import ClientError
from PIL import Image

from dify_plugin import Tool
from dify_plugin.entities.tool import (
    ToolInvokeMessage,
    ToolParameter,
    ToolParameterOption,
    I18nObject,
)
from provider.utils import resolve_aws_credentials, build_boto3_client_kwargs


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NOVA_REEL_DEFAULT_REGION = "us-east-1"
NOVA_REEL_DEFAULT_DIMENSION = "1280x720"
NOVA_REEL_DEFAULT_FPS = 24
NOVA_REEL_DEFAULT_DURATION = 6
NOVA_REEL_MODEL_ID = "amazon.nova-reel-v1:0"
NOVA_REEL_STATUS_CHECK_INTERVAL = 5

# 入力画像の要件（解像度と色空間）
NOVA_REEL_REQUIRED_IMAGE_WIDTH = 1280
NOVA_REEL_REQUIRED_IMAGE_HEIGHT = 720
NOVA_REEL_REQUIRED_IMAGE_MODE = "RGB"


class NovaReelTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """AWS Bedrock Nova Reel モデルを呼び出して動画生成またはステータス情報を返す."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)

            # 入力値を検証しつつ整形
            params = self._validate_and_extract_parameters(tool_parameters)
            if isinstance(params, ToolInvokeMessage):
                yield params

            if params["aws_region"]:
                credentials["aws_region"] = params["aws_region"]

            # Bedrock/S3 クライアントを初期化
            bedrock, s3_client = self._initialize_aws_clients(credentials)

            # モデルへ渡すペイロードを組み立て
            model_input = self._prepare_model_input(params, s3_client)
            if isinstance(model_input, ToolInvokeMessage):
                yield model_input

            # 動画生成を開始
            invocation = self._start_video_generation(bedrock, model_input, params["video_output_s3uri"])
            invocation_arn = invocation["invocationArn"]

            # 非同期/同期モードに応じて結果を返却
            yield self._handle_generation_mode(bedrock, s3_client, invocation_arn, params["async_mode"])

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.exception(f"AWS API error: {error_code} - {error_message}")
            yield self.create_text_message(f"AWS service error: {error_code} - {error_message}")
        except Exception as e:
            logger.error(f"Unexpected error in video generation: {str(e)}", exc_info=True)
            yield self.create_text_message(f"Failed to generate video: {str(e)}")

    def _validate_and_extract_parameters(
        self, tool_parameters: dict[str, Any]
    ) -> Union[dict[str, Any], ToolInvokeMessage]:
        """入力辞書から必要なパラメータを検証し、欠落時はユーザーへ通知する."""
        prompt = tool_parameters.get("prompt", "")
        video_output_s3uri = tool_parameters.get("video_output_s3uri", "").strip()

        # 必須パラメータの存在確認
        if not prompt:
            return self.create_text_message("Please provide a text prompt for video generation.")
        if not video_output_s3uri:
            return self.create_text_message("Please provide an S3 URI for video output.")

        # S3 URI フォーマットを検証
        if not video_output_s3uri.startswith("s3://"):
            return self.create_text_message("Invalid S3 URI format. Must start with 's3://'")

        # S3 パス末尾に必ず `/` を付与
        video_output_s3uri = video_output_s3uri if video_output_s3uri.endswith("/") else video_output_s3uri + "/"

        return {
            "prompt": prompt,
            "video_output_s3uri": video_output_s3uri,
            "image_input_s3uri": tool_parameters.get("image_input_s3uri", "").strip(),
            "aws_region": tool_parameters.get("aws_region", NOVA_REEL_DEFAULT_REGION),
            "dimension": tool_parameters.get("dimension", NOVA_REEL_DEFAULT_DIMENSION),
            "seed": int(tool_parameters.get("seed", 0)),
            "fps": int(tool_parameters.get("fps", NOVA_REEL_DEFAULT_FPS)),
            "duration": int(tool_parameters.get("duration", NOVA_REEL_DEFAULT_DURATION)),
            "async_mode": bool(tool_parameters.get("async", True)),
        }

    def _initialize_aws_clients(self, credentials: dict[str, Optional[str]]) -> tuple[Any, Any]:
        """Bedrock Runtime と S3 クライアントを生成する."""
        client_kwargs = build_boto3_client_kwargs(credentials)
        bedrock = boto3.client(service_name="bedrock-runtime", **client_kwargs)
        s3_client = boto3.client("s3", **client_kwargs)
        return bedrock, s3_client

    def _prepare_model_input(self, params: dict[str, Any], s3_client: Any) -> Union[dict[str, Any], ToolInvokeMessage]:
        """Nova Reel モデルに渡す入力を構築し、必要に応じて画像を前処理する."""
        model_input = {
            "taskType": "TEXT_VIDEO",
            "textToVideoParams": {"text": params["prompt"]},
            "videoGenerationConfig": {
                "durationSeconds": params["duration"],
                "fps": params["fps"],
                "dimension": params["dimension"],
                "seed": params["seed"],
            },
        }

        # 画像が指定されていれば Base64 へ変換し先頭フレームに利用
        if params["image_input_s3uri"]:
            try:
                image_data = self._get_image_from_s3(s3_client, params["image_input_s3uri"])
                if not image_data:
                    return self.create_text_message("Failed to retrieve image from S3")

                # 前処理済み画像オブジェクトを取得し検証
                processed_image = self._process_and_validate_image(image_data)
                if isinstance(processed_image, ToolInvokeMessage):
                    return processed_image

                # PNG へ変換した画像を Base64 エンコード
                img_buffer = BytesIO()
                processed_image.save(img_buffer, format="PNG")
                img_buffer.seek(0)
                input_image_base64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")

                model_input["textToVideoParams"]["images"] = [
                    {"format": "png", "source": {"bytes": input_image_base64}}
                ]
            except Exception as e:
                logger.error(f"Error processing input image: {str(e)}", exc_info=True)
                return self.create_text_message(f"Failed to process input image: {str(e)}")

        return model_input

    def _process_and_validate_image(self, image_data: bytes) -> Union[Image.Image, ToolInvokeMessage]:
        """Nova Reel が要求する 1280x720 RGB 画像へ正規化し、条件を満たさない場合はメッセージを返す."""
        try:
            # 画像を開く
            img = Image.open(BytesIO(image_data))

            # RGBA の場合は透過有無をチェックし、必要なら RGB へ変換
            if img.mode == "RGBA":
                # 透過ピクセルが存在しないか確認
                if img.getchannel("A").getextrema()[0] < 255:
                    return self.create_text_message(
                        "PNG image contains transparent or translucent pixels, which is not supported. "
                        "Please provide an image without transparency."
                    )
                # 問題なければ RGB へ変換
                img = img.convert("RGB")
            elif img.mode != "RGB":
            # その他の色空間も RGB へ統一
                img = img.convert("RGB")

            # 解像度が違えばリサイズ
            if img.size != (NOVA_REEL_REQUIRED_IMAGE_WIDTH, NOVA_REEL_REQUIRED_IMAGE_HEIGHT):
                logger.warning(
                    f"Image dimensions {img.size} do not match required dimensions "
                    f"({NOVA_REEL_REQUIRED_IMAGE_WIDTH}x{NOVA_REEL_REQUIRED_IMAGE_HEIGHT}). Resizing..."
                )
                img = img.resize(
                    (NOVA_REEL_REQUIRED_IMAGE_WIDTH, NOVA_REEL_REQUIRED_IMAGE_HEIGHT), Image.Resampling.LANCZOS
                )

            # ビット深度を確認
            if img.mode != NOVA_REEL_REQUIRED_IMAGE_MODE:
                return self.create_text_message(
                    f"Image must be in {NOVA_REEL_REQUIRED_IMAGE_MODE} mode with 8 bits per channel"
                )

            return img

        except Exception as e:
            logger.error(f"Error processing image: {str(e)}", exc_info=True)
            return self.create_text_message(
                "Failed to process image. Please ensure the image is a valid JPEG or PNG file."
            )

    def _get_image_from_s3(self, s3_client: Any, s3_uri: str) -> Optional[bytes]:
        """S3 から画像バイナリを取得する."""
        parsed_uri = urlparse(s3_uri)
        bucket = parsed_uri.netloc
        key = parsed_uri.path.lstrip("/")

        response = s3_client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def _start_video_generation(self, bedrock: Any, model_input: dict[str, Any], output_s3uri: str) -> dict[str, Any]:
        """非同期動画生成を開始し、Invocation ARN などを返す."""
        return bedrock.start_async_invoke(
            modelId=NOVA_REEL_MODEL_ID,
            modelInput=model_input,
            outputDataConfig={"s3OutputDataConfig": {"s3Uri": output_s3uri}},
        )

    def _handle_generation_mode(
        self, bedrock: Any, s3_client: Any, invocation_arn: str, async_mode: bool
    ) -> ToolInvokeMessage:
        """非同期/同期モード別にレスポンスを整形する."""
        invocation_response = bedrock.get_async_invoke(invocationArn=invocation_arn)
        video_path = invocation_response["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]
        video_uri = f"{video_path}/output.mp4"

        if async_mode:
            return self.create_text_message(
                f"Video generation started.\nInvocation ARN: {invocation_arn}\nVideo will be available at: {video_uri}"
            )

        return self._wait_for_completion(bedrock, s3_client, invocation_arn)

    def _wait_for_completion(self, bedrock: Any, s3_client: Any, invocation_arn: str) -> ToolInvokeMessage:
        """同期モードで生成完了をポーリングし、成功/失敗を判定する."""
        while True:
            status_response = bedrock.get_async_invoke(invocationArn=invocation_arn)
            status = status_response["status"]
            video_path = status_response["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"]

            if status == "Completed":
                return self._handle_completed_video(s3_client, video_path)
            elif status == "Failed":
                failure_message = status_response.get("failureMessage", "Unknown error")
                return self.create_text_message(f"Video generation failed.\nError: {failure_message}")
            elif status == "InProgress":
                time.sleep(NOVA_REEL_STATUS_CHECK_INTERVAL)
            else:
                return self.create_text_message(f"Unexpected status: {status}")

    def _handle_completed_video(self, s3_client: Any, video_path: str) -> ToolInvokeMessage:
        """生成済み動画をダウンロードし、テキスト通知とバイナリを返す."""
        parsed_uri = urlparse(video_path)
        bucket = parsed_uri.netloc
        key = parsed_uri.path.lstrip("/") + "/output.mp4"

        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            video_content = response["Body"].read()
            return [
                self.create_text_message(f"{video_path}/output.mp4 and s3://{bucket}/{key}"),
                self.create_blob_message(blob=video_content, meta={"mime_type": "video/mp4"}, save_as="output.mp4"),
            ]
        except Exception as e:
            logger.error(f"Error downloading video: {str(e)}", exc_info=True)
            return self.create_text_message(
                f"Video generation completed but failed to download video: {str(e)}\n"
                f"Video is available at: s3://{bucket}/{key}"
            )

    def get_runtime_parameters(self) -> list[ToolParameter]:
        """Define the tool's runtime parameters."""
        parameters = [
            ToolParameter(
                name="prompt",
                label=I18nObject(en_US="Prompt", ja_JP="プロンプト"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Text description of the video you want to generate",
                    ja_JP="生成したい動画のテキスト説明",
                ),
                llm_description="Describe the video you want to generate",
            ),
            ToolParameter(
                name="video_output_s3uri",
                label=I18nObject(en_US="Output S3 URI", ja_JP="出力先 S3 URI"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="S3 URI where the generated video will be stored",
                    ja_JP="生成動画を保存する S3 URI",
                ),
            ),
            ToolParameter(
                name="dimension",
                label=I18nObject(en_US="Dimension", ja_JP="解像度"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default=NOVA_REEL_DEFAULT_DIMENSION,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Video dimensions (width x height)",
                    ja_JP="動画の解像度（幅 x 高さ）",
                ),
            ),
            ToolParameter(
                name="duration",
                label=I18nObject(en_US="Duration", ja_JP="再生時間"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=NOVA_REEL_DEFAULT_DURATION,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Video duration in seconds",
                    ja_JP="動画の長さ（秒）",
                ),
            ),
            ToolParameter(
                name="seed",
                label=I18nObject(en_US="Seed", ja_JP="シード値"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=0,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Random seed for video generation",
                    ja_JP="動画生成の乱数シード",
                ),
            ),
            ToolParameter(
                name="fps",
                label=I18nObject(en_US="FPS", ja_JP="FPS"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=NOVA_REEL_DEFAULT_FPS,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Frames per second for the generated video",
                    ja_JP="生成動画のフレームレート",
                ),
            ),
            ToolParameter(
                name="async",
                label=I18nObject(en_US="Async Mode", ja_JP="非同期モード"),
                type=ToolParameter.ToolParameterType.BOOLEAN,
                required=False,
                default=True,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Whether to run in async mode (return immediately) or sync mode (wait for completion)",
                    ja_JP="非同期で実行するか（すぐ返却）/同期で待機するか",
                ),
            ),
            ToolParameter(
                name="aws_region",
                label=I18nObject(en_US="AWS Region", ja_JP="AWS リージョン"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default=NOVA_REEL_DEFAULT_REGION,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="AWS region for Bedrock service",
                    ja_JP="Bedrock サービスの AWS リージョン",
                ),
            ),
            ToolParameter(
                name="image_input_s3uri",
                label=I18nObject(en_US="Input Image S3 URI", ja_JP="入力画像 S3 URI"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="S3 URI of the input image (1280x720 JPEG/PNG) to use as first frame",
                    ja_JP="先頭フレームに使う入力画像（1280x720 JPEG/PNG）の S3 URI",
                ),
            ),
        ]

        return parameters
