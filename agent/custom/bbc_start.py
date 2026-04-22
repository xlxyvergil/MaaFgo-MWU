import os
import sys
import time
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_connection_manager import get_manager
from bbc_emulator_utils import get_connect_command_and_args, kill_bbc_processes
import mfaalog


@AgentServer.custom_action("start_bbc")
class StartBbc(CustomAction):
    """检测BBC状态并传递参数给Manager进行启动/连接"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """
        尝试确保 BBC 服务运行并连接到配置的模拟器；必要时重启 BBC 并重连。

        参数:
            context (Context): 动作上下文；期望节点数据包含键 "启动bbc"，其 `attach` 映射指定连接模式和模拟器参数。
            argv (CustomAction.RunArg): 未使用的运行时参数。

        返回:
            CustomAction.RunResult: 如果模拟器已连接且参数匹配，或 BBC 重启并连接成功返回 `success=True`；失败或异常时返回 `success=False`。
        """
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("启动bbc")
            if not node_data:
                mfaalog.error("[StartBbc] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取连接相关参数（使用共享helper）
            connect_cmd, connect_args = get_connect_command_and_args(attach_data)

            mfaalog.info(f"[StartBbc] 连接参数: cmd={connect_cmd}, args={connect_args}")
            
            # 步骤1: 检查BBC进程是否存在
            mfaalog.info("[StartBbc] 步骤1: 检查BBC状态...")
            
            # 获取或创建 Manager 实例（进程级单例）
            manager = get_manager()
            bbc_proc = manager.find_bbc_process()
            
            if bbc_proc:
                mfaalog.info(f"[StartBbc] 发现BBC进程，PID: {bbc_proc.pid}")
                # 检查Manager是否已连接
                if manager.ensure_connected(timeout=3):
                    mfaalog.info("[StartBbc] Manager已连接，检查模拟器状态...")
                    # 先检查模拟器是否已经连接
                    conn_result = manager.send_command('get_connection', {}, timeout=5)
                    mfaalog.info(f"[StartBbc] get_connection 返回: {conn_result}")
                    
                    # 检查返回结果中是否有模拟器连接信息
                    emulator_ready = False
                    if conn_result and isinstance(conn_result, dict):
                        data = conn_result
                        connected = data.get('connected', False)
                        available = data.get('available', False)
                        device_info = data.get('device_info', {})
                        emulator_params = device_info.get('emulator_params', {})
                        
                        # 有模拟器参数说明已连接到具体模拟器，检查是否与配置匹配
                        if (connected or available) and emulator_params:
                            # 根据连接类型检查参数是否匹配
                            params_match = manager.check_emulator_params_match(connect_cmd, connect_args, emulator_params)
                            if params_match:
                                emulator_ready = True
                                mfaalog.info(f"[StartBbc] 模拟器已连接且参数匹配: {emulator_params}")
                            else:
                                mfaalog.warning(f"[StartBbc] 模拟器参数不匹配，期望: {connect_args}, 实际: {emulator_params}")
                        else:
                            mfaalog.info(f"[StartBbc] 模拟器未连接: connected={connected}, params={emulator_params}")
                    else:
                        mfaalog.warning(f"[StartBbc] 无法获取连接状态: {conn_result}")
                    
                    if emulator_ready:
                        mfaalog.info("[StartBbc] 模拟器已就绪，无需重复连接")
                        return CustomAction.RunResult(success=True)
                    else:
                        # 模拟器未就绪，需要重启BBC
                        mfaalog.warning("[StartBbc] 模拟器未就绪，将重启BBC")
                        # 继续执行重启流程
                else:
                    mfaalog.warning("[StartBbc] Manager未连接，将重启BBC进程")
            
            # 步骤2: Kill掉所有BBC进程（清理残留窗口）
            mfaalog.info("[StartBbc] 步骤2: 清理所有BBC进程...")
            kill_bbc_processes()
            time.sleep(2)
            
            # 步骤3: 调用Manager的完整重启流程
            mfaalog.info("[StartBbc] 步骤3: 调用Manager重启BBC并连接模拟器...")
            success = manager.restart_bbc_and_connect(connect_cmd, connect_args, max_retries=5)
            
            if success:
                mfaalog.info("[StartBbc] BBC启动并连接成功")
                return CustomAction.RunResult(success=True)
            else:
                mfaalog.error("[StartBbc] BBC启动失败")
                return CustomAction.RunResult(success=False)
            
        except Exception as e:
            mfaalog.error(f"[StartBbc] 异常: {e}")
            return CustomAction.RunResult(success=False)
