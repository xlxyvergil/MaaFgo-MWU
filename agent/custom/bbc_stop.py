import psutil
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog


@AgentServer.custom_action("stop_bbc")
class StopBbc(CustomAction):
    """强制关闭BBC进程"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            mfaalog.info("[StopBbc] 正在终止 BBC 进程...")
            
            killed_count = 0
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('BBchannel' in arg for arg in cmdline):
                        mfaalog.info(f"[StopBbc] 找到 BBC 进程 PID: {proc.pid}")
                        # 直接强制杀死进程（terminate无法关闭有弹窗的进程）
                        proc.kill()
                        killed_count += 1
                        mfaalog.info(f"[StopBbc] 已强制杀死 BBC 进程 PID: {proc.pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            if killed_count > 0:
                mfaalog.info(f"[StopBbc] 已终止 {killed_count} 个 BBC 进程")
            else:
                mfaalog.info("[StopBbc] 未找到运行中的 BBC 进程")
            
            return CustomAction.RunResult(success=True)
                    
        except Exception as e:
            mfaalog.error(f"[StopBbc] 终止进程时出错: {e}")
            return CustomAction.RunResult(success=False)
