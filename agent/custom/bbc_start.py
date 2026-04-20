import json
import os
import sys
import time
import logging
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_connection_manager import bbc_manager

# 配置日志输出到文件
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_start_debug.log')

# 创建具名 logger
logger = logging.getLogger("BbcStart")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)


@AgentServer.custom_action("start_bbc")
class StartBbc(CustomAction):
    """检测BBC状态并传递参数给Manager进行启动/连接"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("启动bbc")
            if not node_data:
                logger.error("[StartBbc] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取连接相关参数
            connect = attach_data.get('connect', 'auto')
            mumu_path = attach_data.get('mumu_path', '')
            mumu_index = attach_data.get('mumu_index', 0)
            mumu_pkg = attach_data.get('mumu_pkg', 'com.bilibili.fatego')
            mumu_app_index = attach_data.get('mumu_app_index', 0)
            ld_path = attach_data.get('ld_path', '')
            ld_index = attach_data.get('ld_index', 0)
            manual_port = attach_data.get('manual_port', '')
            
            # 将连接类型转换为 BBC 服务端命令
            connect_cmd_map = {
                'mumu': 'connect_mumu',
                'ld': 'connect_ld',
                'ldplayer': 'connect_ld', 
                'adb': 'connect_adb',
                'manual': 'connect_adb',
                'connect_mumu': 'connect_mumu',
                'connect_ld': 'connect_ld',
                'connect_adb': 'connect_adb'
            }
            connect_cmd = connect_cmd_map.get(connect, connect)
            
            # 构建连接参数
            connect_args = {}
            if connect_cmd == 'connect_mumu':
                connect_args = {
                    'path': mumu_path,
                    'index': int(mumu_index),
                    'pkg': mumu_pkg,
                    'app_index': int(mumu_app_index)
                }
            elif connect_cmd == 'connect_ld':
                connect_args = {
                    'path': ld_path,
                    'index': int(ld_index)
                }
            elif connect_cmd == 'connect_adb':
                connect_args = {
                    'ip': manual_port
                }
            elif connect_cmd == 'auto':
                connect_args = {
                    'mode': 'auto'
                }
            
            logger.info(f"[StartBbc] 连接参数: connect={connect}, cmd={connect_cmd}")
            logger.info(f"[StartBbc] MuMu: path={mumu_path}, index={mumu_index}, pkg={mumu_pkg}")
            logger.info(f"[StartBbc] LD: path={ld_path}, index={ld_index}")
            
            # 步骤1: 检查BBC进程是否存在
            logger.info("[StartBbc] 步骤1: 检查BBC状态...")
            bbc_proc = bbc_manager._find_bbc_process()
            
            if bbc_proc:
                logger.info(f"[StartBbc] 发现BBC进程，PID: {bbc_proc.pid}")
                # 检查Manager是否已连接
                if bbc_manager.ensure_connected(timeout=3):
                    logger.info("[StartBbc] Manager已连接，跳过启动步骤")
                    # 直接连接模拟器
                    if bbc_manager.connect_emulator(connect_cmd, connect_args, timeout=30):
                        logger.info("[StartBbc] 模拟器连接成功")
                        return CustomAction.RunResult(success=True)
                    else:
                        logger.warning("[StartBbc] 模拟器连接失败，需要重启BBC")
                        # 继续执行重启流程
                else:
                    logger.warning("[StartBbc] Manager未连接，将重启BBC进程")
            
            # 步骤2: 调用Manager的完整重启流程
            logger.info("[StartBbc] 调用Manager重启BBC并连接模拟器...")
            success = bbc_manager.restart_bbc_and_connect(connect_cmd, connect_args, max_retries=5)
            
            if success:
                logger.info("[StartBbc] BBC启动并连接成功")
                return CustomAction.RunResult(success=True)
            else:
                logger.error("[StartBbc] BBC启动失败")
                return CustomAction.RunResult(success=False)
            
        except Exception as e:
            logger.error(f"[StartBbc] 异常: {e}", exc_info=True)
            return CustomAction.RunResult(success=False)
