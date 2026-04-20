import json
import os
import sys
import time
import logging
import threading
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
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_debug.log')

# 创建具名 logger
logger = logging.getLogger("BbcAction")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)


# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 事件驱动模式"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("执行BBC任务")
            if not node_data:
                logger.error("[ExecuteBbcTask] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取参数
            team_config = attach_data.get('bbc_team_config', '')
            run_count = attach_data.get('run_count')
            apple_type = attach_data.get('apple_type')
            battle_type = attach_data.get('battle_type', '连续出击')
            support_order_mismatch = attach_data.get('support_order_mismatch', False)
            team_config_error = attach_data.get('team_config_error', False)
            
            # 验证必需参数
            if not team_config or run_count is None or apple_type is None:
                logger.error(f"[ExecuteBbcTask] 参数不完整: team={team_config}, count={run_count}, apple={apple_type}")
                return CustomAction.RunResult(success=False)
            
            run_count = int(run_count)
            logger.info(f"[ExecuteBbcTask] 参数: team={team_config}, count={run_count}, apple={apple_type}, type={battle_type}")
            
            # 步骤1: 尝试TCP连接，失败则触发bbc_start
            if not self._ensure_bbc_connected(context):
                return CustomAction.RunResult(success=False)
            
            # 清空消息队列，避免读取历史弹窗
            bbc_manager.clear_message_queue()
            
            # 步骤2: 验证模拟器连接
            if not self._verify_emulator_connection(attach_data, context):
                bbc_manager.disconnect_tcp()
                return CustomAction.RunResult(success=False)
            
            # 步骤3: 配置并启动战斗（同时启动回调监听）
            state = self._setup_and_start_battle(
                team_config, run_count, apple_type, battle_type,
                support_order_mismatch, team_config_error
            )
            if state is None:
                bbc_manager.disconnect_tcp()
                return CustomAction.RunResult(success=False)
            
            # 步骤4: 等待战斗结束
            popup_title, popup_message = self._wait_for_battle_end(state, state['popup_event'])
            
            bbc_manager.disconnect_tcp()
            
            # 步骤5: 输出结果
            if popup_title or popup_message:
                display_text = f"{popup_title}: {popup_message}" if popup_title else popup_message
                context.override_pipeline({
                    "bbc弹窗信息输出": {
                        "focus": {
                            "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{display_text}</span>"
                        }
                    }
                })
                logger.info(f"[ExecuteBbcTask] 战斗结束: {display_text}")
            else:
                logger.info("[ExecuteBbcTask] 战斗正常结束")
            
            return CustomAction.RunResult(success=True)
            
        except Exception as e:
            logger.error(f"[ExecuteBbcTask] 异常: {e}", exc_info=True)
            return CustomAction.RunResult(success=False)
    
    def _ensure_bbc_connected(self, context: Context):
        """确保BBC已连接，必要时触发bbc_start"""
        # 检查连接是否有效
        if bbc_manager.ensure_connected(timeout=3):
            logger.info("[ExecuteBbcTask] TCP连接有效")
            return True
        
        logger.warning("[ExecuteBbcTask] TCP连接失效，触发bbc_start...")
        
        # 触发bbc_start pipeline节点
        result = context.run_task("启动bbc")
        if not result:
            logger.error("[ExecuteBbcTask] bbc_start执行失败")
            return False
        
        # 重新检查连接
        time.sleep(2)
        if bbc_manager.ensure_connected(timeout=5):
            logger.info("[ExecuteBbcTask] bbc_start后TCP连接成功")
            return True
        
        logger.error("[ExecuteBbcTask] bbc_start后TCP仍连接失败")
        return False
    
    def _verify_emulator_connection(self, attach_data: dict, context: Context) -> bool:
        """验证模拟器连接，必要时调用Manager重启"""
        conn_status = bbc_manager.send_command('get_connection', {}, timeout=5)
        
        # get_connection 直接返回连接状态，没有 success 字段
        if conn_status.get('connected') or conn_status.get('available'):
            logger.info("[ExecuteBbcTask] 模拟器已连接，跳过连接步骤")
            return True
        
        logger.warning("[ExecuteBbcTask] 模拟器未连接，调用Manager重启BBC...")
        
        # 提取连接参数
        connect = attach_data.get('connect', 'auto')
        connect_cmd_map = {
            'mumu': 'connect_mumu',
            'ld': 'connect_ld',
            'adb': 'connect_adb',
            'connect_mumu': 'connect_mumu',
            'connect_ld': 'connect_ld',
            'connect_adb': 'connect_adb'
        }
        connect_cmd = connect_cmd_map.get(connect, connect)
        
        connect_args = {}
        if connect_cmd == 'connect_mumu':
            connect_args = {
                'path': attach_data.get('mumu_path', ''),
                'index': int(attach_data.get('mumu_index', 0)),
                'pkg': attach_data.get('mumu_pkg', 'com.bilibili.fatego'),
                'app_index': int(attach_data.get('mumu_app_index', 0))
            }
        elif connect_cmd == 'connect_ld':
            connect_args = {
                'path': attach_data.get('ld_path', ''),
                'index': int(attach_data.get('ld_index', 0))
            }
        elif connect_cmd == 'connect_adb':
            connect_args = {
                'ip': attach_data.get('manual_port', '')
            }
        elif connect_cmd == 'auto':
            connect_args = {
                'mode': 'auto'
            }
        
        # 调用Manager的完整重启流程
        success = bbc_manager.restart_bbc_and_connect(connect_cmd, connect_args, max_retries=3)
        
        if success:
            logger.info("[ExecuteBbcTask] BBC重启并连接成功")
            return True
        else:
            logger.error("[ExecuteBbcTask] BBC重启失败")
            return False
    
    def _setup_and_start_battle(self, team_config: str, run_count: int, 
                                apple_type: str, battle_type: str,
                                support_order_mismatch: bool, team_config_error: bool) -> dict:
        """配置战斗参数并启动，返回 state 或 None"""
        
        # 共享状态
        state = {
            'finished': False,
            'popup_title': '',
            'popup_message': '',
            'popup_event': threading.Event()  # 弹窗事件
        }
        
        # 设置弹窗回调
        def on_popup(msg):
            """弹窗回调函数 - 快速返回，不阻塞监听线程"""
            logger.info(f"[ExecuteBbcTask] 收到弹窗: {msg.get('popup_title', '')}")
            if not state['finished']:
                self._handle_popups([msg], False, False, state)
                state['popup_event'].set()  # 通知主线程
        
        bbc_manager.set_popup_callback(on_popup)
        logger.info("[ExecuteBbcTask] 弹窗回调已注册")
        
        # 加载配置
        logger.info(f"[ExecuteBbcTask] 加载配置: {team_config}")
        result = bbc_manager.send_command('load_config', {'filename': team_config}, timeout=10)
        if not result.get('success'):
            logger.error(f"[ExecuteBbcTask] 加载配置失败: {result.get('error')}")
            return None
        
        # 检查配置阶段是否有弹窗
        popup_msgs = bbc_manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state):
            return state
        
        # 设置参数
        logger.info(f"[ExecuteBbcTask] 设置苹果类型: {apple_type}")
        bbc_manager.send_command('set_apple_type', {'apple_type': apple_type}, timeout=5)
        
        popup_msgs = bbc_manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state):
            return state
        
        logger.info(f"[ExecuteBbcTask] 设置运行次数: {run_count}")
        bbc_manager.send_command('set_run_times', {'times': run_count}, timeout=5)
        
        logger.info(f"[ExecuteBbcTask] 设置战斗类型: {battle_type}")
        bbc_manager.send_command('set_battle_type', {'battle_type': battle_type}, timeout=5)
        
        # 启动战斗前最后检查一次弹窗
        popup_msgs = bbc_manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state):
            return state
        
        # 启动战斗（带重试机制）
        logger.info("[ExecuteBbcTask] 启动战斗...")
        max_retries = 3
        battle_started = False
        
        for retry in range(max_retries):
            # 发送启动命令
            result = bbc_manager.send_command('start_battle', {}, timeout=10)
            if not result.get('success'):
                error = result.get('error', '')
                logger.error(f"[ExecuteBbcTask] 启动战斗命令失败: {error}")
                
                # 检查是否是阵容未设置错误
                if 'Servant slot' in error:
                    logger.warning(f"[ExecuteBbcTask] 阵容未设置，重新触发点击 ({retry+1}/{max_retries})")
                    time.sleep(2)
                    continue
                else:
                    return None
            
            # 等待并检查状态
            time.sleep(2)
            ui_status = bbc_manager.send_command('get_ui_status', {}, timeout=5)
            
            # 检查是否成功启动
            if ui_status.get('battle_running') or ui_status.get('device_running'):
                logger.info("[ExecuteBbcTask] 战斗已启动")
                battle_started = True
                break
            
            # 检查UI提示文本
            top_label = ui_status.get('top_label', '')
            logger.info(f"[ExecuteBbcTask] UI状态: {top_label}")
            
            if '前辈！请设置好阵容再出战哦！' in top_label:
                logger.warning(f"[ExecuteBbcTask] 检测到阵容未设置提示，重新触发点击 ({retry+1}/{max_retries})")
                time.sleep(2)
                continue
            
            # 检查是否有其他弹窗
            popup_msgs = bbc_manager.get_messages_by_title('', timeout=2)
            if popup_msgs:
                logger.info(f"[ExecuteBbcTask] 检测到弹窗: {popup_msgs[0].get('popup_title', '')}")
        
        if not battle_started:
            logger.error("[ExecuteBbcTask] 启动战斗失败，已达到最大重试次数")
            return None
        
        logger.info("[ExecuteBbcTask] 战斗已启动，等待结束...")
        return state
    
    def _handle_popups(self, messages: list, support_order_mismatch: bool, 
                      team_config_error: bool, state: dict) -> bool:
        """处理弹窗消息列表，返回是否遇到终止弹窗"""
        for msg in messages:
            popup_title = msg.get('popup_title', '')
            popup_message = msg.get('popup_message', '')
            popup_id = msg.get('popup_id', '')
            
            logger.info(f"[Callback] 收到弹窗: {popup_title}")
            
            # 处理助战排序不符合
            if '助战排序不符合' in popup_title:
                action = 'ok' if support_order_mismatch else 'cancel'
                logger.info(f"[Callback] 助战弹窗，响应: {action}")
                
                if popup_id:
                    bbc_manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if action == 'cancel':
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    logger.info("[Callback] 用户拒绝助战，战斗结束")
                    return True
            
            # 处理队伍配置错误
            elif '队伍配置错误' in popup_title:
                action = 'ok' if team_config_error else 'cancel'
                logger.info(f"[Callback] 队伍配置弹窗，响应: {action}")
                
                if popup_id:
                    bbc_manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if action == 'cancel':
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    logger.info("[Callback] 用户拒绝队伍配置，战斗结束")
                    return True
            
            # 处理脚本停止
            elif '脚本停止' in popup_title:
                logger.info(f"[Callback] 检测到脚本停止: {popup_message}")
                
                if popup_id:
                    bbc_manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': 'ok'
                    }, timeout=5)
                
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                logger.info("[Callback] 脚本停止已处理，战斗结束")
                return True
            
            # 处理正在结束任务
            elif '正在结束任务' in popup_title:
                logger.info(f"[Callback] 检测到正在结束任务: {popup_message}")
                
                if popup_id:
                    bbc_manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': 'ok'
                    }, timeout=5)
                
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                logger.info("[Callback] 正在结束任务已处理，战斗结束")
                return True
            
            # 处理其他任务运行中
            elif '其他任务运行中' in popup_title:
                logger.warning(f"[Callback] 检测到其他任务运行中: {popup_message}")
                
                if popup_id:
                    bbc_manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': 'ok'
                    }, timeout=5)
                
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                logger.info("[Callback] 其他任务运行中，战斗结束")
                return True
        
        return False
    
    def _wait_for_battle_end(self, state: dict, popup_event: threading.Event):
        """等待战斗结束 - 心跳检查和弹窗处理分离"""
        
        # 启动弹窗监听线程（独立运行，即时响应）
        def popup_listener():
            while not state['finished']:
                popup_event.wait()  # 无限等待弹窗
                popup_event.clear()
                logger.info("[ExecuteBbcTask] 弹窗监听线程收到通知")
                # 弹窗已在回调中处理，这里只需记录日志
        
        listener_thread = threading.Thread(target=popup_listener, daemon=True)
        listener_thread.start()
        logger.info("[ExecuteBbcTask] 弹窗监听线程已启动")
        
        # 主线程：只做心跳检查
        heartbeat_interval = 30  # 30秒一次心跳
        while not state['finished']:
            time.sleep(heartbeat_interval)
            
            # 心跳检查
            status = bbc_manager.send_command('get_status', {}, timeout=5)
            if not status.get('success'):
                logger.warning("[ExecuteBbcTask] BBC服务无响应")
                state['finished'] = True
                state['popup_title'] = '错误'
                state['popup_message'] = 'BBC服务异常'
                break
            
            logger.debug("[ExecuteBbcTask] 心跳检查正常")
        
        return state['popup_title'], state['popup_message']
    
