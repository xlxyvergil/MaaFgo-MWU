import os
import psutil
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context


@AgentServer.custom_action("stop_bbc")
class StopBbc(CustomAction):
    """强制关闭BBC进程"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            print("[StopBbc] 正在终止 BBC 进程...")
            
            killed_count = 0
            
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('BBchannel.exe' in arg for arg in cmdline):
                        print(f"[StopBbc] 找到 BBC 进程 PID: {proc.pid}")
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                            killed_count += 1
                        except psutil.TimeoutExpired:
                            proc.kill()
                            killed_count += 1
                            print(f"[StopBbc] 强制杀死 BBC 进程 PID: {proc.pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            if killed_count > 0:
                print(f"[StopBbc] 已终止 {killed_count} 个 BBC 进程")
            else:
                print("[StopBbc] 未找到运行中的 BBC 进程")
            
            return CustomAction.RunResult(success=True)
                    
        except Exception as e:
            print(f"[StopBbc] 终止进程时出错: {e}")
            return CustomAction.RunResult(success=False)
