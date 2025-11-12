"""
場所: provider/logging_filters.py
内容: ログ出力から AWS 資格情報などの機密文字列を自動でマスクするフィルターと補助関数。
目的: tool_parameters を含む例外ロギングで AK/SK が平文で残らないようにする。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from dify_plugin.config.logger_format import plugin_logger_handler

MASK_TOKEN = "***REDACTED***"
SENSITIVE_FIELD_NAMES = (
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_sts_token",
    "aws_ak",
    "aws_sk",
    "access_key_id",
    "secret_access_key",
    "secret_key",
    "access_key",
)
_SENSITIVE_FIELD_SET = {name.lower() for name in SENSITIVE_FIELD_NAMES}
_FIELD_PATTERN = "|".join(re.escape(name) for name in SENSITIVE_FIELD_NAMES)

_QUOTED_FIELD_PATTERN = re.compile(
    rf"(?P<prefix>\"?(?:{_FIELD_PATTERN})\"?\s*[:=]\s*)(?P<quote>[\"']) (?P<value>[^\"']+?)(?P=quote)",
    flags=re.IGNORECASE | re.VERBOSE,
)
_UNQUOTED_FIELD_PATTERN = re.compile(
    rf"(?P<prefix>\"?(?:{_FIELD_PATTERN})\"?\s*[:=]\s*)(?P<value>[^\s,}}]+)",
    flags=re.IGNORECASE,
)
_ACCESS_KEY_VALUE_PATTERN = re.compile(r"\b(A3T|ABIA|ACCA|AGPA|AIDA|AKIA|ANPA|ANVA|APKA|ASIA)[0-9A-Z]{16}\b")


def mask_sensitive_text(message: str) -> str:
    """文字列内の機密値をマスクする."""

    if not message:
        return message

    def _replace_with_mask(match: re.Match[str]) -> str:
        value = match.group("value")
        if value == MASK_TOKEN:
            return match.group(0)
        quote = match.groupdict().get("quote", "")
        return f"{match.group('prefix')}{quote}{MASK_TOKEN}{quote}"

    masked = _QUOTED_FIELD_PATTERN.sub(_replace_with_mask, message)
    masked = _UNQUOTED_FIELD_PATTERN.sub(
        lambda m: m.group(0) if m.group("value") == MASK_TOKEN else f"{m.group('prefix')}{MASK_TOKEN}",
        masked,
    )
    masked = _ACCESS_KEY_VALUE_PATTERN.sub(MASK_TOKEN, masked)
    return masked


def scrub_sensitive_data(data: Any) -> Any:
    """辞書やリストを再帰的に走査し、機密キーのみマスクしたコピーを返す."""

    if isinstance(data, Mapping):
        sanitized: dict[Any, Any] = {}
        for key, value in data.items():
            key_lower = key.lower() if isinstance(key, str) else None
            if key_lower and key_lower in _SENSITIVE_FIELD_SET:
                sanitized[key] = MASK_TOKEN
            else:
                sanitized[key] = scrub_sensitive_data(value)
        return sanitized

    if isinstance(data, (list, tuple, set)):
        sanitized_items = [scrub_sensitive_data(item) for item in data]
        if isinstance(data, tuple):
            return tuple(sanitized_items)
        if isinstance(data, set):
            return set(sanitized_items)
        return sanitized_items

    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="ignore")

    if isinstance(data, str):
        return mask_sensitive_text(data)

    return data


class SensitiveDataFilter(logging.Filter):
    """logging.Filter 実装。record を書き出す前にマスクを適用する."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.msg = scrub_sensitive_data(record.msg)
        if record.args:
            record.args = scrub_sensitive_data(record.args)

        for attr in ("data", "payload", "context"):
            if hasattr(record, attr):
                setattr(record, attr, scrub_sensitive_data(getattr(record, attr)))

        return True


_FILTER_INSTANCE: SensitiveDataFilter | None = None


def install_sensitive_data_filter() -> SensitiveDataFilter:
    """プラグインのストリームハンドラとルートロガーにフィルターを一度だけ組み込む."""

    global _FILTER_INSTANCE
    if _FILTER_INSTANCE is not None:
        return _FILTER_INSTANCE

    _FILTER_INSTANCE = SensitiveDataFilter()
    logging.getLogger().addFilter(_FILTER_INSTANCE)
    plugin_logger_handler.addFilter(_FILTER_INSTANCE)
    return _FILTER_INSTANCE
