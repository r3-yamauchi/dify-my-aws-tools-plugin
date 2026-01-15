"""
CloudWatch Logs 時刻処理ユーティリティ

Unix タイムスタンプ、ISO 8601、相対時刻の解析機能を提供します。
"""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Union


class TimeUtils:
    """時刻処理のためのユーティリティクラス"""
    
    # 相対時刻のパターン（例: "1h", "30m", "2d", "1w"）
    RELATIVE_TIME_PATTERN = re.compile(r'^(\d+)([smhdw])$')
    
    # 時間単位の秒数マッピング
    TIME_UNITS = {
        's': 1,          # 秒
        'm': 60,         # 分
        'h': 3600,       # 時間
        'd': 86400,      # 日
        'w': 604800,     # 週
    }
    
    @staticmethod
    def parse_time_input(time_input: Union[str, int, None]) -> Optional[int]:
        """
        複数の時刻形式を Unix timestamp (ms) に変換
        
        Args:
            time_input: 時刻入力（Unix timestamp ms、ISO8601文字列、相対時刻、またはNone）
            
        Returns:
            Unix timestamp (ms) または None
            
        Raises:
            ValueError: 無効な時刻形式の場合
        """
        if time_input is None:
            return None
            
        # 既に Unix timestamp (ms) の場合
        if isinstance(time_input, int):
            # 妥当性チェック（1970年以降、2100年以前）
            if 0 <= time_input <= 4102444800000:  # 2100-01-01 00:00:00 UTC
                return time_input
            else:
                raise ValueError(f"Unix タイムスタンプが範囲外です: {time_input}")
        
        if isinstance(time_input, str):
            time_input = time_input.strip()
            
            # 相対時刻の解析（例: "1h", "30m", "2d"）
            relative_match = TimeUtils.RELATIVE_TIME_PATTERN.match(time_input.lower())
            if relative_match:
                amount = int(relative_match.group(1))
                unit = relative_match.group(2)
                
                if unit not in TimeUtils.TIME_UNITS:
                    raise ValueError(f"サポートされていない時間単位です: {unit}")
                
                # 現在時刻から相対時刻を計算
                now = datetime.now(timezone.utc)
                seconds_offset = amount * TimeUtils.TIME_UNITS[unit]
                target_time = now - timedelta(seconds=seconds_offset)
                return int(target_time.timestamp() * 1000)
            
            # ISO 8601 形式の解析
            try:
                # 複数のISO 8601形式をサポート
                iso_formats = [
                    "%Y-%m-%dT%H:%M:%S.%fZ",      # 2024-01-03T10:30:45.123Z
                    "%Y-%m-%dT%H:%M:%SZ",         # 2024-01-03T10:30:45Z
                    "%Y-%m-%dT%H:%M:%S.%f%z",     # 2024-01-03T10:30:45.123+09:00
                    "%Y-%m-%dT%H:%M:%S%z",        # 2024-01-03T10:30:45+09:00
                    "%Y-%m-%d %H:%M:%S",          # 2024-01-03 10:30:45 (UTC想定)
                    "%Y-%m-%d",                   # 2024-01-03 (00:00:00 UTC想定)
                ]
                
                for fmt in iso_formats:
                    try:
                        if fmt.endswith('%z'):
                            # タイムゾーン情報あり
                            dt = datetime.strptime(time_input, fmt)
                        else:
                            # タイムゾーン情報なし（UTCとして扱う）
                            dt = datetime.strptime(time_input, fmt)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                        
                        return int(dt.timestamp() * 1000)
                    except ValueError:
                        continue
                
                # すべての形式で解析に失敗
                raise ValueError(f"サポートされていない時刻形式です: {time_input}")
                
            except Exception as e:
                raise ValueError(f"時刻の解析に失敗しました: {time_input} - {str(e)}")
        
        raise ValueError(f"サポートされていない時刻入力タイプです: {type(time_input)}")
    
    @staticmethod
    def to_iso8601(timestamp_ms: int) -> str:
        """
        Unix timestamp (ms) を ISO8601 文字列に変換
        
        Args:
            timestamp_ms: Unix timestamp (ms)
            
        Returns:
            ISO8601 形式の文字列（UTC、ミリ秒精度）
        """
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:-3] + "Z"  # ミリ秒精度に調整
    
    @staticmethod
    def validate_time_range(start_time: Optional[int], end_time: Optional[int]) -> Tuple[bool, str]:
        """
        時刻範囲の妥当性を検証
        
        Args:
            start_time: 開始時刻 (Unix timestamp ms)
            end_time: 終了時刻 (Unix timestamp ms)
            
        Returns:
            (妥当性フラグ, エラーメッセージ)
        """
        if start_time is None and end_time is None:
            return True, ""
        
        if start_time is not None and end_time is not None:
            if start_time >= end_time:
                return False, "開始時刻は終了時刻より前である必要があります"
        
        # 現在時刻より未来の時刻をチェック
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        
        if start_time is not None and start_time > now_ms:
            return False, "開始時刻は現在時刻より未来に設定できません"
        
        if end_time is not None and end_time > now_ms:
            return False, "終了時刻は現在時刻より未来に設定できません"
        
        # 過去すぎる時刻をチェック（CloudWatch Logsの制限を考慮）
        # CloudWatch Logsは通常14日間のデータ保持がデフォルト
        fourteen_days_ago = now_ms - (14 * 24 * 60 * 60 * 1000)
        
        if start_time is not None and start_time < fourteen_days_ago:
            # 警告レベル（エラーではない）
            pass
        
        return True, ""
    
    @staticmethod
    def format_duration(start_time_ms: int, end_time_ms: int) -> str:
        """
        時間範囲を人間が読みやすい形式でフォーマット
        
        Args:
            start_time_ms: 開始時刻 (Unix timestamp ms)
            end_time_ms: 終了時刻 (Unix timestamp ms)
            
        Returns:
            フォーマットされた期間文字列
        """
        duration_ms = end_time_ms - start_time_ms
        duration_seconds = duration_ms / 1000
        
        if duration_seconds < 60:
            return f"{duration_seconds:.1f}秒"
        elif duration_seconds < 3600:
            return f"{duration_seconds / 60:.1f}分"
        elif duration_seconds < 86400:
            return f"{duration_seconds / 3600:.1f}時間"
        else:
            return f"{duration_seconds / 86400:.1f}日"