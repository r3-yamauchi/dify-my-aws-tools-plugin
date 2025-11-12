"""
場所: tools/agentcore_code_interpreter.py
内容: Bedrock AgentCore Code Interpreter を呼び出し、コマンド実行やコード実行を行うツール。
目的: Dify からコードインタープリタを起動し、セッション管理と結果取得を一貫して行えるようにする。
"""

from collections.abc import Generator
from typing import Any
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
import boto3
import json
import time
from provider.utils import resolve_aws_credentials

class AgentcoreCodeInterpreterTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        credentials = resolve_aws_credentials(self, tool_parameters)
        merged_params = dict(tool_parameters)
        merged_params.setdefault("aws_access_key_id", credentials.get("aws_access_key_id"))
        merged_params.setdefault("aws_secret_access_key", credentials.get("aws_secret_access_key"))
        merged_params.setdefault("aws_region", credentials.get("aws_region"))

        result = self.execute(**merged_params)
        yield self.create_json_message(result)


    def execute(
        self,
        language=None,
        code=None,
        command=None,
        session_id=None,
        code_interpreter_id=None,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        aws_region=None,
        **kwargs,
    ):
        error_msg = ""
        
        try:
            # 1. AWS 資格情報に応じたクライアントを生成
            data_client = self.create_client(aws_access_key_id, aws_secret_access_key, aws_region, 'bedrock-agentcore')
            
            # 2. Code Interpreter が無ければ作成
            if not code_interpreter_id:
                control_client = self.create_client(aws_access_key_id, aws_secret_access_key, aws_region, 'bedrock-agentcore-control')
                code_interpreter_id = self.create_code_interpreter(control_client)
            
            # 3. セッションを新規作成する
            if not session_id:
                session_id = self.init_session(data_client, code_interpreter_id)
            
            # 4. コマンド→コードの順番で実行
            results = []
            
            if command:
                command_result = self.exec_command_internal(data_client, code_interpreter_id, session_id, command)
                results.append({"type": "command", "result": command_result})
            
            if code and language:
                code_result = self.exec_code_internal(data_client, code_interpreter_id, session_id, language, code)
                results.append({"type": "code", "result": code_result})
            
            if not command and not code:
                raise ValueError("Either command or code must be provided")
                
        except Exception as e:
            error_msg = str(e)
        
        if error_msg:
            result = {"status": "error", "reason": str(error_msg)}
        else:
            result = {
                "status": "success", 
                "session_id": session_id, 
                "code_interpreter_id": code_interpreter_id,
                "results": results
            }
        
        return result

    def create_client(
        self,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_region: str | None = None,
        service_name: str = 'bedrock-agentcore',
    ):
        """必要に応じて認証情報を付与した boto3 クライアントを作る."""
        kwargs: dict[str, Any] = {}
        if aws_region:
            kwargs['region_name'] = aws_region
        if aws_access_key_id and aws_secret_access_key:
            kwargs['aws_access_key_id'] = aws_access_key_id
            kwargs['aws_secret_access_key'] = aws_secret_access_key
        return boto3.client(service_name, **kwargs)

    def create_code_interpreter(self, client):
        """新しい Code Interpreter を生成する."""
        timestamp = int(time.time())
        response = client.create_code_interpreter(
            name=f'code_interpreter_{timestamp}',
            description='code-interpreter with network access',
            networkConfiguration={'networkMode': 'PUBLIC'}
        )
        return response.get('codeInterpreterId')

    def exec_code_internal(self, client, code_interpreter_id, session_id, language, code):
        """コード断片を実行し、結果を返す."""
        arguments = {
            "language": language,
            "code": code
        }
        response = client.invoke_code_interpreter(
            codeInterpreterIdentifier=code_interpreter_id,
            name="executeCode",
            sessionId=session_id,
            arguments=arguments
        )
        return self.get_tool_result(response)

    def exec_command_internal(self, client, code_interpreter_id, session_id, command):
        """シェルコマンドを実行する."""
        arguments = {
            "command": command
        }
        response = client.invoke_code_interpreter(
            codeInterpreterIdentifier=code_interpreter_id,
            name="executeCommand",
            sessionId=session_id,
            arguments=arguments
        )
        return self.get_tool_result(response)


    def init_session(self, data_client, ci_id):
        try:
            response = data_client.start_code_interpreter_session(codeInterpreterIdentifier=ci_id, sessionTimeoutSeconds=900)
            session_id = response['sessionId']
        except Exception as e:
            raise
        return session_id


    def get_tool_result(self, response):
        try:
            if "stream" in response:
                event_stream = response["stream"]
                for event in event_stream:
                    if "result" in event:
                        result = event["result"]
                        return str(result)
        except Exception as e:
            return f"tool result error: {e}"
