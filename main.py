"""
場所: main.py
内容: Dify プラグインの起動ポイントとロギングフィルター初期化。
目的: プロセス開始時に機密情報マスクを有効にし、安全な形でプラグインを実行する。
"""

from dify_plugin import Plugin, DifyPluginEnv

from provider.logging_filters import install_sensitive_data_filter

install_sensitive_data_filter()

plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=120))

if __name__ == '__main__':
    plugin.run()
