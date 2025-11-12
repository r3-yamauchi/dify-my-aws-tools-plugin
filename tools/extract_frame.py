"""
場所: tools/extract_frame.py
内容: GIF ファイルから均等間隔で指定枚数のフレームを抽出し、Dify から扱える PNG で返す。
目的: URL で渡された GIF を一時保存し、Workflow へ画像バイナリを配信できるようにする。
"""

from typing import Any, Generator
import os
import shutil
import requests
from PIL import Image

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

class FrameExtractor(Tool):
    def _extract_specific_frames(self, gif_path, output_folder, frame_count=5):
        """GIF から指定枚数のフレームを均等に抜き出し、PNG ファイルで保存する補助."""
        # 出力フォルダが無ければ作成
        os.makedirs(output_folder, exist_ok=True)

        # GIF を開く
        gif = Image.open(gif_path)

        # 総フレーム数を取得
        total_frames = gif.n_frames
        print(f"GIF共有 {total_frames} 帧")

        # 抽出するフレーム番号の配列を計算
        if frame_count == 2:
            # 2 枚のみ指定された場合は先頭と末尾を返す
            frames_to_extract = [0, total_frames - 1]
        else:
            # それ以外は等間隔で算出
            if frame_count >= total_frames:
                # 抽出枚数が総数以上ならすべて返す
                frames_to_extract = list(range(total_frames))
            else:
                # 総数に応じて間隔を決定
                step = (total_frames - 1) / (frame_count - 1) if frame_count > 1 else 0
                frames_to_extract = [int(i * step) for i in range(frame_count)]
                # 最終フレームが含まれるよう補正
                if frames_to_extract[-1] != total_frames - 1:
                    frames_to_extract[-1] = total_frames - 1

        # 実際にフレームを抽出して保存
        extracted_paths = []
        for i, frame_idx in enumerate(frames_to_extract):
            gif.seek(frame_idx)
            frame = gif.copy()
            output_path = os.path.join(output_folder, f"frame_{i:03d}.png")
            frame.save(output_path)
            extracted_paths.append(output_path)
            print(f"已保存第 {frame_idx+1}/{total_frames} 帧 (索引 {frame_idx})")

        print(f"已提取 {len(extracted_paths)} 帧!")
        return extracted_paths

    def _clean_temp_dir(self, temp_dir):
        """一時ディレクトリを削除するユーティリティ."""
        try:
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                print(f"已删除临时目录: {temp_dir}")
        except Exception as e:
            print(f"删除临时目录时出错: {str(e)}")

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage, None, None]:
        """入力 URL から GIF を取得し、抽出フレームを PNG として返却する."""
        temp_dir = os.path.join(os.path.dirname(__file__), "temp")
        try:
            input_url = tool_parameters.get("input_url")
            frame_count = int(tool_parameters.get("frame_count", 5))  # 默认提取5帧
            input_type = tool_parameters.get("input_type", "GIF")  # 默认为GIF类型

            # 一時ディレクトリを作成
            os.makedirs(temp_dir, exist_ok=True)

            # 入力 GIF やフレームを書き出すパスを用意
            gif_path = os.path.join(temp_dir, "input.gif")
            output_folder = os.path.join(temp_dir, "frames")

            # 入力タイプごとの処理
            if input_type == "GIF":
                # URL から GIF をダウンロード
                response = requests.get(input_url, stream=True)
                if response.status_code == 200:
                    with open(gif_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            f.write(chunk)
                else:
                    yield self.create_text_message(f"下载GIF失败 - {input_url}，状态码: {response.status_code}")
            else:
                yield self.create_text_message(f"只支持GIF格式。")

            # フレームを抽出
            extracted_paths = self._extract_specific_frames(gif_path, output_folder, frame_count)

            # PNG をバイナリとして返却
            for path in extracted_paths:
                with open(path, 'rb') as f:
                    frame_content = f.read()
                    yield self.create_blob_message(
                        blob=frame_content, 
                        meta={"mime_type": "image/png"}
                    )
                    
        except Exception as e:
            yield self.create_text_message(f"提取帧时出错: {str(e)}")
        finally:
            # 成否に関わらず一時ディレクトリを削除
            self._clean_temp_dir(temp_dir)
