import os
import sys
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_emulator_utils import kill_bbc_processes


@AgentServer.custom_action("stop_bbc")
class StopBbc(CustomAction):
    """强制关闭BBC进程"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """
        终止命令行包含 'BBchannel' 的所有运行中进程。

        返回:
            CustomAction.RunResult: 动作完成无错误返回 `success=True`，否则返回 `success=False`。
        """
        try:
            mfaalog.info("[StopBbc] 正在终止 BBC 进程...")
            kill_bbc_processes()
            return CustomAction.RunResult(success=True)
        except Exception as e:
            mfaalog.error(f"[StopBbc] 终止进程时出错: {e}")
            return CustomAction.RunResult(success=False)