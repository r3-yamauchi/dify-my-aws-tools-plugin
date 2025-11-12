"""
場所: tools/transcribe_asr.py
内容: Amazon Transcribe を使った音声文字起こしワークフローを実装し、音声ダウンロード→S3 取り込み→ジョブ実行→結果取得までを自動化する。
目的: Dify から URL だけで音声をアップロードし、スピーカーダイアライゼーション付きテキストを取得できるようにする。
"""

import json
import logging
import os
import re
import time
import uuid
import requests
from requests.exceptions import RequestException
from typing import Any, Union
from urllib.parse import urlparse
from collections.abc import Generator

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


LanguageCodeOptions = [
    "af-ZA",
    "ar-AE",
    "ar-SA",
    "da-DK",
    "de-CH",
    "de-DE",
    "en-AB",
    "en-AU",
    "en-GB",
    "en-IE",
    "en-IN",
    "en-US",
    "en-WL",
    "es-ES",
    "es-US",
    "fa-IR",
    "fr-CA",
    "fr-FR",
    "he-IL",
    "hi-IN",
    "id-ID",
    "it-IT",
    "ja-JP",
    "ko-KR",
    "ms-MY",
    "nl-NL",
    "pt-BR",
    "pt-PT",
    "ru-RU",
    "ta-IN",
    "te-IN",
    "tr-TR",
    "zh-CN",
    "zh-TW",
    "th-TH",
    "en-ZA",
    "en-NZ",
    "vi-VN",
    "sv-SE",
    "ab-GE",
    "ast-ES",
    "az-AZ",
    "ba-RU",
    "be-BY",
    "bg-BG",
    "bn-IN",
    "bs-BA",
    "ca-ES",
    "ckb-IQ",
    "ckb-IR",
    "cs-CZ",
    "cy-WL",
    "el-GR",
    "et-ET",
    "eu-ES",
    "fi-FI",
    "gl-ES",
    "gu-IN",
    "ha-NG",
    "hr-HR",
    "hu-HU",
    "hy-AM",
    "is-IS",
    "ka-GE",
    "kab-DZ",
    "kk-KZ",
    "kn-IN",
    "ky-KG",
    "lg-IN",
    "lt-LT",
    "lv-LV",
    "mhr-RU",
    "mi-NZ",
    "mk-MK",
    "ml-IN",
    "mn-MN",
    "mr-IN",
    "mt-MT",
    "no-NO",
    "or-IN",
    "pa-IN",
    "pl-PL",
    "ps-AF",
    "ro-RO",
    "rw-RW",
    "si-LK",
    "sk-SK",
    "sl-SI",
    "so-SO",
    "sr-RS",
    "su-ID",
    "sw-BI",
    "sw-KE",
    "sw-RW",
    "sw-TZ",
    "sw-UG",
    "tl-PH",
    "tt-RU",
    "ug-CN",
    "uk-UA",
    "uz-UZ",
    "wo-SN",
    "zu-ZA",
]

MediaFormat = ["mp3", "mp4", "wav", "flac", "ogg", "amr", "webm", "m4a"]


def is_url(text):
    if not text:
        return False
    text = text.strip()
    # URL 判定用の正規表現
    pattern = re.compile(
        r"^"  # 文字列の先頭
        r"(?:http|https)://"  # http/https のみ許可
        r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|"  # ドメイン部
        r"localhost|"  # localhost
        r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # IPアドレス
        r"(?::\d+)?"  # 任意のポート
        r"(?:/?|[/?]\S+)"  # パス
        r"$",  # 文字列の末尾
        re.IGNORECASE,
    )
    return bool(pattern.match(text))


def upload_file_from_url_to_s3(s3_client, url, bucket_name, s3_key=None, max_retries=3):
    """URL からファイルを取得し、S3 へアップロードする（リトライ付き）。"""

    # 入力パラメータのバリデーション
    if not url or not bucket_name:
        return False, "URL and bucket name are required"

    retry_count = 0
    while retry_count < max_retries:
        try:
            # URL からファイルをダウンロード
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()

            # s3_key が無ければ URL からファイル名を推測
            if not s3_key:
                parsed_url = urlparse(url)
                filename = os.path.basename(parsed_url.path.split("/file-preview")[0])
                s3_key = "transcribe-files/" + filename

            # S3 へアップロード
            s3_client.upload_fileobj(
                response.raw,
                bucket_name,
                s3_key,
                ExtraArgs={
                    "ContentType": response.headers.get("content-type"),
                    "ACL": "private",  # 常に private で保存
                },
            )

            return f"s3://{bucket_name}/{s3_key}", f"Successfully uploaded file to s3://{bucket_name}/{s3_key}"

        except RequestException as e:
            retry_count += 1
            if retry_count == max_retries:
                return None, f"Failed to download file from URL after {max_retries} attempts: {str(e)}"
            continue

        except ClientError as e:
            return None, f"AWS S3 error: {str(e)}"

        except Exception as e:
            return None, f"Unexpected error: {str(e)}"

    return None, "Maximum retries exceeded"


class TranscribeTool(Tool):
    s3_client: Any = None
    transcribe_client: Any = None

    """LanguageCode / IdentifyLanguage / IdentifyMultipleLanguages のうち 1 つのみ指定する必要がある。"""

    def _transcribe_audio(self, audio_file_uri, file_type, **extra_args):
        uuid_str = str(uuid.uuid4())
        job_name = f"{int(time.time())}-{uuid_str}"
        try:
            # Transcribe ジョブを起動
            response = self.transcribe_client.start_transcription_job(
                TranscriptionJobName=job_name, Media={"MediaFileUri": audio_file_uri}, **extra_args
            )

            # 完了するまでポーリング
            while True:
                status = self.transcribe_client.get_transcription_job(TranscriptionJobName=job_name)
                if status["TranscriptionJob"]["TranscriptionJobStatus"] in ["COMPLETED", "FAILED"]:
                    break
                time.sleep(5)

            if status["TranscriptionJob"]["TranscriptionJobStatus"] == "COMPLETED":
                return status["TranscriptionJob"]["Transcript"]["TranscriptFileUri"], None
            else:
                return None, f"Error: TranscriptionJobStatus:{status['TranscriptionJob']['TranscriptionJobStatus']} "

        except Exception as e:
            return None, f"Error: {str(e)}"

    def _download_and_read_transcript(self, transcript_file_uri: str, max_retries: int = 3) -> tuple[str, str]:
        """トランスクリプトの JSON を取得し、必要に応じて話者ラベル付き文字列へ整形する."""
        retry_count = 0
        while retry_count < max_retries:
            try:
                # Transcribe が返した URI から JSON を取得
                response = requests.get(transcript_file_uri, timeout=30)
                response.raise_for_status()

                # JSON をパース
                transcript_data = response.json()

                # スピーカーダイアライゼーションが存在するか確認
                has_speaker_labels = (
                    "results" in transcript_data
                    and "speaker_labels" in transcript_data["results"]
                    and "segments" in transcript_data["results"]["speaker_labels"]
                )

                if has_speaker_labels:
                    # 話者セグメントと単語リストを取得
                    segments = transcript_data["results"]["speaker_labels"]["segments"]
                    items = transcript_data["results"]["items"]

                    # start_time と speaker_label の対応表を作る
                    time_to_speaker = {}
                    for segment in segments:
                        speaker_label = segment["speaker_label"]
                        for item in segment["items"]:
                            time_to_speaker[item["start_time"]] = speaker_label

                    # 話者ラベル付きの文字列を構築
                    current_speaker = None
                    transcript_parts = []

                    for item in items:
                        # 句読点など発話以外はそのまま追加
                        if item["type"] == "punctuation":
                            transcript_parts.append(item["alternatives"][0]["content"])
                            continue

                        start_time = item["start_time"]
                        speaker = time_to_speaker.get(start_time)

                        if speaker != current_speaker:
                            current_speaker = speaker
                            transcript_parts.append(f"\n[{speaker}]: ")

                        transcript_parts.append(item["alternatives"][0]["content"])

                    return " ".join(transcript_parts).strip(), None
                else:
                    # 通常のテキストは results -> transcripts 配列に含まれる
                    if "results" in transcript_data and "transcripts" in transcript_data["results"]:
                        transcripts = transcript_data["results"]["transcripts"]
                        if transcripts:
                            # 各セグメントを結合
                            full_text = " ".join(t.get("transcript", "") for t in transcripts)
                            return full_text, None

                return None, "No transcripts found in the response"

            except requests.exceptions.RequestException as e:
                retry_count += 1
                if retry_count == max_retries:
                    return None, f"Failed to download transcript file after {max_retries} attempts: {str(e)}"
                continue

            except json.JSONDecodeError as e:
                return None, f"Failed to parse transcript JSON: {str(e)}"

            except Exception as e:
                return None, f"Unexpected error while processing transcript: {str(e)}"

        return None, "Maximum retries exceeded"

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """
        invoke tools
        """
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")

            reset_clients_on_credential_change(
                self,
                credentials,
                ["transcribe_client", "s3_client"],
            )

            if not self.transcribe_client or not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                if not self.transcribe_client:
                    self.transcribe_client = boto3.client("transcribe", **client_kwargs)
                if not self.s3_client:
                    self.s3_client = boto3.client("s3", **client_kwargs)

            file_url = tool_parameters.get("file_url")
            file_type = tool_parameters.get("file_type")
            language_code = tool_parameters.get("language_code")
            identify_language = tool_parameters.get("identify_language", True)
            identify_multiple_languages = tool_parameters.get("identify_multiple_languages", False)
            language_options_str = tool_parameters.get("language_options")
            s3_bucket_name = tool_parameters.get("s3_bucket_name")
            ShowSpeakerLabels = tool_parameters.get("ShowSpeakerLabels", True)
            MaxSpeakerLabels = tool_parameters.get("MaxSpeakerLabels", 2)

            # 入力パラメータの整合性チェック
            if not s3_bucket_name:
                yield self.create_text_message(text="s3_bucket_name is required")
            language_options = None
            if language_options_str:
                language_options = language_options_str.split("|")
                for lang in language_options:
                    if lang not in LanguageCodeOptions:
                        yield self.create_text_message(
                            text=f"{lang} is not supported, should be one of {LanguageCodeOptions}"
                        )
            if language_code and language_code not in LanguageCodeOptions:
                err_msg = f"language_code:{language_code} is not supported, should be one of {LanguageCodeOptions}"
                yield self.create_text_message(text=err_msg)

            err_msg = f"identify_language:{identify_language}, \
                identify_multiple_languages:{identify_multiple_languages}, \
                Note that you must include one of LanguageCode, IdentifyLanguage, \
                or IdentifyMultipleLanguages in your request. \
                If you include more than one of these parameters, \
                your transcription job fails."
            if not language_code:
                if identify_language and identify_multiple_languages:
                    yield self.create_text_message(text=err_msg)
            else:
                if identify_language or identify_multiple_languages:
                    yield self.create_text_message(text=err_msg)

            extra_args = {
                "IdentifyLanguage": identify_language,
                "IdentifyMultipleLanguages": identify_multiple_languages,
            }
            if language_code:
                extra_args["LanguageCode"] = language_code
            if language_options:
                extra_args["LanguageOptions"] = language_options
            if ShowSpeakerLabels:
                extra_args["Settings"] = {"ShowSpeakerLabels": ShowSpeakerLabels, "MaxSpeakerLabels": MaxSpeakerLabels}

            # S3 バケットへファイルをアップロード
            s3_path_result, error = upload_file_from_url_to_s3(self.s3_client, url=file_url, bucket_name=s3_bucket_name)
            if not s3_path_result:
                yield self.create_text_message(text=error)

            transcript_file_uri, error = self._transcribe_audio(
                audio_file_uri=s3_path_result,
                file_type=file_type,
                **extra_args,
            )
            if not transcript_file_uri:
                yield self.create_text_message(text=error)

            # 生成されたトランスクリプトをダウンロードして読み込む
            transcript_text, error = self._download_and_read_transcript(transcript_file_uri)
            if not transcript_text:
                yield self.create_text_message(text=error)

            yield self.create_text_message(text=transcript_text)

        except Exception as e:
            yield self.create_text_message(f"Exception {str(e)}")
