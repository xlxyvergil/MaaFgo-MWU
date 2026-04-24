import os
import sys
import time
import threading
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

from bbc_connection_manager import get_manager
import mfaalog


# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 事件驱动模式"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """主入口：带自动重启的战斗流程"""
        max_retries = 2  # 最多重试2次
        last_error = None
        
        for attempt in range(max_retries):
            if attempt > 0:
                mfaalog.warning(f"[ExecuteBbcTask] 第{attempt}次重试...")
                # 执行BBC重启
                if not self._restart_bbc(context):
                    return CustomAction.RunResult(success=False)
            
            # 执行单次战斗流程
            result = self._execute_single_battle(context)
            last_error = result.get('error', '')
            
            # 检查是否需要重启
            if result.get('need_restart', False):
                mfaalog.warning("[ExecuteBbcTask] 检测到游戏异常，准备重启...")
                continue  # 进入下一次循环
            else:
                # 返回最终结果
                if result['success']:
                    return CustomAction.RunResult(success=True)
                else:
                    # 失败时输出错误信息
                    if last_error:
                        context.override_pipeline({
                            "bbc弹窗信息输出": {
                                "focus": {
                                    "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{last_error}</span>"
                                }
                            }
                        })
                    return CustomAction.RunResult(success=False)
        
        # 达到最大重试次数
        error_msg = f"战斗失败（已重试{max_retries-1}次）" + (f": {last_error}" if last_error else "")
        mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
        context.override_pipeline({
            "bbc弹窗信息输出": {
                "focus": {
                    "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">{error_msg}</span>"
                }
            }
        })
        return CustomAction.RunResult(success=False)
    
    def _execute_single_battle(self, context: Context) -> dict:
        """执行单次战斗流程（不含重启逻辑）"""
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("执行BBC任务")
            if not node_data:
                mfaalog.error("[ExecuteBbcTask] 无法获取节点数据")
                return {'success': False, 'error': '无法获取节点数据'}
            
            attach_data = node_data.get('attach', {})
            
            # 提取参数
            team_config = attach_data.get('bbc_team_config', '')
            run_count = attach_data.get('run_count')
            apple_type = attach_data.get('apple_type')
            battle_type = attach_data.get('battle_type', '连续出击')
            
            # 直接使用配置文件中的布尔值（BBC Server 需要 True/False）
            support_order_mismatch = attach_data.get('support_order_mismatch', False)
            team_config_error = attach_data.get('team_config_error', False)
            
            # 验证必需参数
            if not team_config or run_count is None or apple_type is None:
                error_msg = f"参数不完整: team={team_config}, count={run_count}, apple={apple_type}"
                mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
                return {'success': False, 'error': error_msg}
            
            run_count = int(run_count)
            mfaalog.info(f"[ExecuteBbcTask] 参数: team={team_config}, count={run_count}, apple={apple_type}, type={battle_type}")
            
            # 提前创建共享状态并启动监听线程
            popup_event = threading.Event()
            state = {
                'finished': False,
                'popup_title': '',
                'popup_message': '',
                'popup_event': popup_event
            }
            
            # 启动弹窗监听线程（在 action 开头启动，确保能捕获所有弹窗）
            def popup_listener():
                while not state['finished']:
                    popup_event.wait()  # 无限等待弹窗
                    popup_event.clear()
                    mfaalog.info("[ExecuteBbcTask] 弹窗监听线程收到通知")
            
            listener_thread = threading.Thread(target=popup_listener, daemon=True)
            listener_thread.start()
            mfaalog.info("[ExecuteBbcTask] 弹窗监听线程已启动")
            
            # 获取或创建 Manager 实例（进程级单例）
            manager = get_manager()
            
            # 步顤1: 尝试TCP连接，失败则触发bbc_start
            if not self._ensure_bbc_connected(context):
                return {'success': False, 'error': 'BBC连接失败'}
            
            # 提前设置弹窗回调（在清空队列之前，确保不会错过弹窗）
            def on_popup(msg):
                """弹窗回调函数 - 快速返回，不阻塞监听线程"""
                popup_title = msg.get('popup_title', '')
                popup_message = msg.get('popup_message', '')
                mfaalog.info(f"[ExecuteBbcTask] 收到弹窗: {popup_title} - {popup_message}")
                if not state['finished']:
                    try:
                        # 尝试获取 manager 实例
                        local_manager = get_manager()
                        self._handle_popups([msg], support_order_mismatch, team_config_error, state, local_manager)
                    except Exception as e:
                        mfaalog.error(f"[ExecuteBbcTask] 处理弹窗时出错: {e}")
                    state['popup_event'].set()  # 通知监听线程
            
            manager.set_popup_callback(on_popup)
            mfaalog.info("[ExecuteBbcTask] 弹窗回调已设置")
            
            # 清空消息队列，避免读取历史弹窗
            manager.clear_message_queue()
            
            # 步顤2: 验证模拟器连接
            if not self._verify_emulator_connection(attach_data, context):
                manager.disconnect_tcp()
                return {'success': False, 'error': '模拟器连接失败'}
            
            # 步骤3: 配置并启动战斗（同时启动回调监听）
            battle_result = self._setup_and_start_battle(
                team_config, run_count, apple_type, battle_type,
                support_order_mismatch, team_config_error, state, manager
            )
            if battle_result is None:
                manager.disconnect_tcp()
                return {'success': False, 'error': '战斗启动失败'}
            
            # 步骤4: 等待战斗结束
            popup_title, popup_message = self._wait_for_battle_end(state)
            
            manager.disconnect_tcp()
            
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
                mfaalog.info(f"[ExecuteBbcTask] 战斗结束: {display_text}")
            else:
                mfaalog.info("[ExecuteBbcTask] 战斗正常结束")
            
            # 返回结果和是否需要重启的标志
            # 如果是错误状态，标记为失败
            if popup_title == '错误' or '错误' in (popup_message or ''):
                return {
                    'success': False,
                    'error': f"{popup_title}: {popup_message}",
                    'need_restart': True  # 错误状态需要重启
                }
            
            return {
                'success': True,
                'need_restart': state.get('need_restart', False)
            }
            
        except Exception as e:
            error_msg = f"异常: {str(e)}"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            return {'success': False, 'error': error_msg}
    
    def _restart_bbc(self, context: Context) -> bool:
        """重启BBC进程"""
        manager = get_manager()
        try:
            # 先断开当前连接
            manager.disconnect_tcp()
            time.sleep(1)
            
            mfaalog.info("[Restart] 停止BBC进程...")
            stop_result = context.run_task("停止bbc")
            if not stop_result:
                mfaalog.error("[Restart] 停止BBC失败")
                return False
            
            # 等待进程完全退出
            time.sleep(3)
            
            mfaalog.info("[Restart] 启动BBC进程...")
            start_result = context.run_task("启动bbc")
            if not start_result:
                mfaalog.error("[Restart] 启动BBC失败")
                return False
            
            time.sleep(3)
            mfaalog.info("[Restart] BBC重启完成")
            return True
            
        except Exception as e:
            mfaalog.error(f"[Restart] 重启异常: {e}")
            return False
    
    def _ensure_bbc_connected(self, context: Context):
        """确保BBC已连接，必要时触发bbc_start"""
        manager = get_manager()
        # 检查连接是否有效
        if manager.ensure_connected(timeout=3):
            mfaalog.info("[ExecuteBbcTask] TCP连接有效")
            return True
        
        mfaalog.warning("[ExecuteBbcTask] TCP连接失效，触发bbc_start...")
        
        # 触发bbc_start pipeline节点
        result = context.run_task("启动bbc")
        if not result:
            mfaalog.error("[ExecuteBbcTask] bbc_start执行失败")
            return False
        
        # 重新检查连接
        time.sleep(2)
        if manager.ensure_connected(timeout=5):
            mfaalog.info("[ExecuteBbcTask] bbc_start后TCP连接成功")
            return True
        
        mfaalog.error("[ExecuteBbcTask] bbc_start后TCP仍连接失败")
        return False
    
    def _verify_emulator_connection(self, attach_data: dict, context: Context) -> bool:
        """验证模拟器连接，必要时调用Manager重启"""
        manager = get_manager()
        conn_status = manager.send_command('get_connection', {}, timeout=5)
        
        # 检查是否有模拟器参数
        device_info = conn_status.get('device_info', {})
        emulator_params = device_info.get('emulator_params', {})
        
        if emulator_params:
            # 提取用户配置的参数
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
            
            expected_args = {}
            if connect_cmd == 'connect_mumu':
                expected_args = {
                    'path': attach_data.get('mumu_path', ''),
                    'index': int(attach_data.get('mumu_index', 0)),
                    'pkg': attach_data.get('mumu_pkg', 'com.bilibili.fatego'),
                    'app_index': int(attach_data.get('mumu_app_index', 0))
                }
            elif connect_cmd == 'connect_ld':
                expected_args = {
                    'path': attach_data.get('ld_path', ''),
                    'index': int(attach_data.get('ld_index', 0))
                }
            elif connect_cmd == 'connect_adb':
                expected_args = {
                    'ip': attach_data.get('manual_port', '')
                }
            
            # 检查参数是否匹配
            params_match = manager.check_emulator_params_match(connect_cmd, expected_args, emulator_params)
            if params_match:
                mfaalog.info(f"[ExecuteBbcTask] 模拟器已连接且参数匹配: {emulator_params}")
                return True
            else:
                mfaalog.warning(f"[ExecuteBbcTask] 模拟器参数不匹配，期望: {expected_args}, 实际: {emulator_params}")
        
        mfaalog.warning("[ExecuteBbcTask] 模拟器未连接或参数不匹配，调用Manager重启BBC...")
        
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
        success = manager.restart_bbc_and_connect(connect_cmd, connect_args, max_retries=3)
        
        if success:
            mfaalog.info("[ExecuteBbcTask] BBC重启并连接成功")
            return True
        else:
            mfaalog.error("[ExecuteBbcTask] BBC重启失败")
            return False
    
    def _setup_and_start_battle(self, team_config: str, run_count: int, 
                                apple_type: str, battle_type: str,
                                support_order_mismatch: bool, team_config_error: bool,
                                state: dict, manager) -> dict:
        
        # 回调已在 _execute_single_battle 中设置，这里直接使用
        
        # 加载配置
        mfaalog.info(f"[ExecuteBbcTask] 加载配置: {team_config}")
        result = manager.send_command('load_config', {'filename': team_config}, timeout=10)
        if not result.get('success'):
            mfaalog.error(f"[ExecuteBbcTask] 加载配置失败: {result.get('error')}")
            return None
        
        # 检查配置阶段是否有弹窗
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        # 设置参数
        mfaalog.info(f"[ExecuteBbcTask] 设置苹果类型: {apple_type}")
        manager.send_command('set_apple_type', {'apple_type': apple_type}, timeout=5)
        
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        mfaalog.info(f"[ExecuteBbcTask] 设置运行次数: {run_count}")
        manager.send_command('set_run_times', {'times': run_count}, timeout=5)
        
        mfaalog.info(f"[ExecuteBbcTask] 设置战斗类型: {battle_type}")
        manager.send_command('set_battle_type', {'battle_type': battle_type}, timeout=5)
        
        # 启动战斗前最后检查一次弹窗
        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state
        
        # 启动战斗（带重试机制）
        mfaalog.info("[ExecuteBbcTask] 启动战斗...")
        max_retries = 3
        battle_started = False
        
        for retry in range(max_retries):
            # 发送启动命令
            result = manager.send_command('start_battle', {}, timeout=10)
            if not result.get('success'):
                error = result.get('error', '')
                mfaalog.error(f"[ExecuteBbcTask] 启动战斗命令失败: {error}")
                
                # 检查是否是阵容未设置错误
                if 'Servant slot' in error:
                    mfaalog.warning(f"[ExecuteBbcTask] 阵容未设置，重新触发点击 ({retry+1}/{max_retries})")
                    time.sleep(2)
                    continue
                else:
                    return None
            
            # 等待并检查状态
            time.sleep(2)
            ui_status = manager.send_command('get_ui_status', {}, timeout=5)
            
            # 检查是否成功启动
            if ui_status.get('battle_running') or ui_status.get('device_running'):
                mfaalog.info("[ExecuteBbcTask] 战斗已启动")
                battle_started = True
                break
            
            # 检查UI提示文本
            top_label = ui_status.get('top_label', '')
            mfaalog.info(f"[ExecuteBbcTask] UI状态: {top_label}")
            
            if '前辈！请设置好阵容再出战哦！' in top_label:
                mfaalog.warning(f"[ExecuteBbcTask] 检测到阵容未设置提示，重新触发点击 ({retry+1}/{max_retries})")
                time.sleep(2)
                continue
            
            # 检查是否有其他弹窗
            popup_msgs = manager.get_messages_by_title('', timeout=2)
            if popup_msgs:
                mfaalog.info(f"[ExecuteBbcTask] 检测到弹窗: {popup_msgs[0].get('popup_title', '')}")
        
        if not battle_started:
            error_msg = "启动战斗失败（阵容未设置，已重试3次）"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            return {'success': False, 'error': error_msg, 'need_restart': True}
        
        mfaalog.info("[ExecuteBbcTask] 战斗已启动，等待结束...")
        return state
    
    def _handle_popups(self, messages: list, support_order_mismatch: bool, 
                      team_config_error: bool, state: dict, manager) -> bool:
        for msg in messages:
            popup_title = msg.get('popup_title', '')
            popup_message = msg.get('popup_message', '')
            popup_id = msg.get('popup_id', '')
            
            mfaalog.info(f"[Callback] 收到弹窗: {popup_title}")
            
            # 处理助战排序不符合 (askyesno 类型，使用布尔值 True/False)
            if '助战排序不符合' in popup_title:
                action = support_order_mismatch  # True 或 False
                mfaalog.info(f"[Callback] 助战弹窗(askyesno)，响应: {action}")
                
                if popup_id:
                    manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if not action:  # False = 停止
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    mfaalog.info("[Callback] 用户拒绝助战，战斗结束")
                    return True
                else:  # True = 继续
                    state['popup_title'] = ''
                    state['popup_message'] = ''
                    mfaalog.info("[Callback] 用户选择继续助战")
            
            # 处理队伍配置错误 (askokcancel 类型，使用布尔值 True/False)
            elif '队伍配置错误' in popup_title:
                action = team_config_error  # True 或 False
                mfaalog.info(f"[Callback] 队伍配置弹窗(askokcancel)，响应: {action}")
                
                if popup_id:
                    manager.send_command('popup_response', {
                        'popup_id': popup_id,
                        'action': action
                    }, timeout=5)
                
                if not action:  # False = 停止
                    state['finished'] = True
                    state['popup_title'] = popup_title
                    state['popup_message'] = popup_message
                    mfaalog.info("[Callback] 用户拒绝队伍配置，战斗结束")
                    return True
                else:  # True = 继续
                    state['popup_title'] = ''
                    state['popup_message'] = ''
                    mfaalog.info("[Callback] 用户选择继续队伍配置")
            
            # 处理脚本停止 (showwarning 类型，BBC Server自动关闭)
            elif '脚本停止' in popup_title:
                mfaalog.info(f"[Callback] 检测到脚本停止: {popup_message}")
                
                # 检查是否是游戏异常导致的脚本停止
                if any(keyword in popup_message for keyword in [
                    '疑似游戏已闪退',
                    '疑似模拟器崩溃',
                    '高速接口获取截图失败'
                ]):
                    mfaalog.error(f"[Callback] 游戏异常导致的脚本停止: {popup_message}")
                    state['finished'] = True
                    state['popup_title'] = '游戏异常'
                    state['popup_message'] = popup_message
                    state['need_restart'] = True  # 标记需要重启
                    return True
                else:
                    mfaalog.info("[Callback] 任务正常结束")
                    # 正常结束不设置弹窗信息，避免被误判为异常
                    state['finished'] = True
                    return True
            
            # 处理其他单按钮弹窗 (showwarning/showerror/showinfo 类型，BBC Server自动关闭)
            elif any(keyword in popup_title for keyword in ['正在结束任务', '其他任务运行中']):
                mfaalog.info(f"[Callback] 检测到提示弹窗: {popup_title}")
                
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                mfaalog.info("[Callback] 提示弹窗已处理，战斗结束")
                return True
            
            # 处理未知弹窗（兜底）
            else:
                mfaalog.warning(f"[Callback] 未知弹窗: {popup_title}")
                # BBC Server 会自动关闭，标记为结束
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                return True
        
        return False
    
    def _wait_for_battle_end(self, state: dict):
        """等待战斗结束 - 心跳检查和弹窗处理分离"""
        
        # 获取 manager 实例
        manager = get_manager()
        
        # 主线程：只做心跳检查
        heartbeat_interval = 30  # 30秒一次心跳
        while not state['finished']:
            time.sleep(heartbeat_interval)
            
            # 心跳检查
            status = manager.send_command('get_status', {}, timeout=5)
            if not status.get('success'):
                mfaalog.warning("[ExecuteBbcTask] BBC服务无响应")
                state['finished'] = True
                state['popup_title'] = '错误'
                state['popup_message'] = 'BBC服务异常'
                state['need_restart'] = True  # 标记需要重启BBC
                break
            
            mfaalog.debug("[ExecuteBbcTask] 心跳检查正常")
        
        return state['popup_title'], state['popup_message']
    
