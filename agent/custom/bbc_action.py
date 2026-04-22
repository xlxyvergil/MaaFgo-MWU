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
from bbc_emulator_utils import get_connect_command_and_args
import mfaalog


# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 事件驱动模式"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """
        执行 BBC 战斗任务，支持最多两次尝试，两次尝试之间可选重启 BBC。

        如果单次尝试信号需要重启，则重启 BBC 并重试。 最终失败时，格式化错误信息写入 pipeline 节点 "bbc弹窗信息输出"。

        参数:
            context (Context): MAA 上下文对象。
            argv (CustomAction.RunArg): 运行时参数。

        返回:
            CustomAction.RunResult: 战斗成功完成返回 `success=True`，否则返回 `success=False`。
        """
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
        """
        使用当前节点的 attach 数据执行单次 BBC 战斗流程，不执行任何 BBC 进程重启。

        执行连接验证、模拟器校验、战斗配置/启动、弹窗事件处理，并等待战斗完成。
        完成后可通过 context.override_pipeline 将弹窗信息写入 pipeline（键为 "bbc弹窗信息输出"）。

        参数:
            context (Context): MAA 上下文对象。

        返回:
            dict: 结果对象，包含以下键：
                - 'success' (bool): 战斗无错误弹窗完成时为 `True`，否则为 `False`。
                - 'error' (str, 可选): 'success' 为 `False` 时的错误信息。
                - 'need_restart' (bool, 可选): 结果表明需要重启 BBC 进程时为 `True`（例如错误弹窗或检测到服务故障），否则为 `False` 或省略。
        """
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
            
            # 提前创建共享状态
            state = {
                'finished': False,
                'popup_title': '',
                'popup_message': ''
            }
            
            # 获取或创建 Manager 实例（进程级单例）
            manager = get_manager()
            
            # 步顤1: 尝试TCP连接，失败则触发bbc_start
            if not self._ensure_bbc_connected(context):
                return {'success': False, 'error': 'BBC连接失败'}
            
            # 提前设置弹窗回调（在清空队列之前，确保不会错过弹窗）
            def on_popup(msg):
                """
                当战斗未结束时，将收到的弹窗消息委托给弹窗处理器。

                参数:
                    msg (dict): 弹窗消息，包含 'popup_title'、'popup_message'、'popup_id' 等键。
                """
                popup_title = msg.get('popup_title', '')
                popup_message = msg.get('popup_message', '')
                mfaalog.info(f"[ExecuteBbcTask] 收到弹窗: {popup_title} - {popup_message}")
                if not state['finished']:
                    self._handle_popups([msg], support_order_mismatch, team_config_error, state, manager)
                    # Check if state became finished after handling to avoid further work
                    if state['finished']:
                        return
            
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

            # Check if battle_result is a failure dict with success==False
            if isinstance(battle_result, dict) and not battle_result.get('success', True):
                manager.disconnect_tcp()
                return battle_result  # Preserve 'need_restart' and 'error'

            # 步骤4: 等待战斗结束
            popup_title, popup_message = self._wait_for_battle_end(state, manager)
            
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
            # 如果是用户主动停止，不算成功
            if state.get('user_stopped'):
                return {
                    'success': False,
                    'error': f"{popup_title}: {popup_message}" if popup_title or popup_message else "用户主动停止",
                    'need_restart': False  # 用户主动停止不需要重启
                }

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
        """
        通过停止再启动 BBC pipeline 任务来重启 BBC 服务。

        参数:
            context (Context): MAA 上下文对象。

        返回:
            bool: 重启成功返回 `True`，否则返回 `False`。
        """
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
        """
        确保与 BBC 服务的 TCP 连接已建立，若未连接则触发 pipeline 节点 "启动bbc"。

        参数:
            context (Context): MAA 上下文对象。

        返回:
            bool: TCP 连接建立成功返回 `True`，否则返回 `False`。
        """
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
        """
        验证模拟器参数是否与预期配置匹配，若参数缺失或不匹配则尝试重启 BBC 并使用 manager 的重连逻辑重新连接。

        参数:
            attach_data (dict): 预期连接配置（包含连接模式和特定provider的参数如 MuMu/LD/ADB 参数），用于构建预期的连接命令和参数。
            context (Context): 动作上下文（传递给任务执行）。

        返回:
            bool: 模拟器参数匹配预期配置，或 manager 成功重启 BBC 并重连时返回 `True`，否则返回 `False`。
        """
        manager = get_manager()
        conn_status = manager.send_command('get_connection', {}, timeout=5)

        # 检查是否有模拟器参数
        device_info = conn_status.get('device_info', {})
        emulator_params = device_info.get('emulator_params', {})

        # 提取用户配置的参数（使用共享helper）
        connect_cmd, expected_args = get_connect_command_and_args(attach_data)

        if emulator_params:
            # 检查参数是否匹配
            params_match = manager.check_emulator_params_match(connect_cmd, expected_args, emulator_params)
            if params_match:
                mfaalog.info(f"[ExecuteBbcTask] 模拟器已连接且参数匹配: {emulator_params}")
                return True
            else:
                mfaalog.warning(f"[ExecuteBbcTask] 模拟器参数不匹配，期望: {expected_args}, 实际: {emulator_params}")

        mfaalog.warning("[ExecuteBbcTask] 模拟器未连接或参数不匹配，调用Manager重启BBC...")

        # 调用Manager的完整重启流程
        success = manager.restart_bbc_and_connect(connect_cmd, expected_args, max_retries=3)

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
        """
        准备战斗配置、应用参数、启动战斗并返回共享状态。

        加载配置文件、设置苹果类型、运行次数、战斗类型，然后发送启动命令。战斗开始后返回共享的 state 字典。

        参数:
            team_config (str): 要加载的队伍配置文件的文件名或标识符。
            run_count (int): 战斗运行次数。
            apple_type (str): 启动前应用的苹果类型设置。
            battle_type (str): 要应用的战斗模式标签（例如 "连续出击"）。
            support_order_mismatch (bool): 若为 True，自动接受助战排序不匹配的弹窗；若为 False，遇到弹窗时停止并设置 `state`。
            team_config_error (bool): 若为 True，自动接受队伍配置错误的弹窗；若为 False，遇到弹窗时停止并设置 `state`。
            state (dict): 用于协调弹窗处理和信号完成的共享可变状态；此函数可能修改 'finished'、'popup_title'、'popup_message'、'need_restart' 等键。
            manager: 用于发送命令和查询 UI/消息的外部 BBC/连接管理器。

        返回:
            dict 或 None: 战斗已启动且完成处理在其他地方时返回共享的 `state` 字典。
            在不可恢复的设置/启动失败时返回 None（例如加载配置失败或启动命令错误）。
            当启动确定失败且建议重启时，可能返回 `{'success': False, 'error': <message>, 'need_restart': True}`。
        """
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
        result = manager.send_command('set_apple_type', {'apple_type': apple_type}, timeout=5)
        if not result.get('success'):
            error_msg = f"set_apple_type RPC failed: {result.get('error', 'unknown error')}, payload: {result}"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            state['finished'] = True
            state['popup_title'] = '错误'
            state['popup_message'] = error_msg
            return state

        popup_msgs = manager.get_messages_by_title('', timeout=1)
        if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
            return state

        mfaalog.info(f"[ExecuteBbcTask] 设置运行次数: {run_count}")
        result = manager.send_command('set_run_times', {'times': run_count}, timeout=5)
        if not result.get('success'):
            error_msg = f"set_run_times RPC failed: {result.get('error', 'unknown error')}, payload: {result}"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            state['finished'] = True
            state['popup_title'] = '错误'
            state['popup_message'] = error_msg
            return state

        mfaalog.info(f"[ExecuteBbcTask] 设置战斗类型: {battle_type}")
        result = manager.send_command('set_battle_type', {'battle_type': battle_type}, timeout=5)
        if not result.get('success'):
            error_msg = f"set_battle_type RPC failed: {result.get('error', 'unknown error')}, payload: {result}"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            state['finished'] = True
            state['popup_title'] = '错误'
            state['popup_message'] = error_msg
            return state
        
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
            if not state['finished']:
                popup_msgs = manager.get_messages_by_title('', timeout=2)
                if popup_msgs:
                    mfaalog.info(f"[ExecuteBbcTask] 检测到弹窗: {popup_msgs[0].get('popup_title', '')}")
                    # Handle the queued popup and check if it finished the run
                    if self._handle_popups(popup_msgs, support_order_mismatch, team_config_error, state, manager):
                        # Run has finished, stop retrying
                        return state
        
        if not battle_started:
            error_msg = "启动战斗失败（阵容未设置，已重试3次）"
            mfaalog.error(f"[ExecuteBbcTask] {error_msg}")
            failure_result = {'success': False, 'error': error_msg, 'need_restart': True}
            # Check if this failure result should short-circuit in _execute_single_battle
            if isinstance(failure_result, dict) and not failure_result.get('success', True):
                return failure_result  # Preserve 'need_restart' and 'error'

        mfaalog.info("[ExecuteBbcTask] 战斗已启动，等待结束...")
        return state
    
    def _handle_popups(self, messages: list, support_order_mismatch: bool,
                      team_config_error: bool, state: dict, manager) -> bool:
        """
        处理 BBC 弹窗消息并根据情况更新共享状态。

        处理已知弹窗类别（助战排序不匹配、队伍配置错误、脚本停止、任务/其他运行通知），通过 manager 可选发送弹窗响应，并设置 state 字段以指示完成状态和任何错误/重启需求。

        参数:
            messages (list): 弹窗消息字典列表；每个可能包含 'popup_title'、'popup_message'、'popup_id'。
            support_order_mismatch (bool): 对 "助战排序不符合" 弹窗的操作；True 继续，False 停止。
            team_config_error (bool): 对 "队伍配置错误" 弹窗的操作；True 继续，False 停止。
            state (dict): 此函数修改的共享状态。可能更新的键：
                - 'finished' (bool): 处理确定运行应该结束时设为 True。
                - 'popup_title' (str): 导致终止的弹窗标题（如果有）。
                - 'popup_message' (str): 导致终止的弹窗消息文本（如果有）。
                - 'need_restart' (bool): 当需要 BBC 重启的崩溃条件时设为 True。
            manager: BBC 管理器，用于通过 'popup_response' 命令发送弹窗响应。

        返回:
            bool: 处理消息导致运行结束时（设置了 state['finished']）返回 `True`，否则返回 `False`。
        """
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
                    state['user_stopped'] = True  # 用户主动停止，不算成功
                    if 'finished_event' in state:
                        state['finished_event'].set()
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
                    state['user_stopped'] = True  # 用户主动停止，不算成功
                    if 'finished_event' in state:
                        state['finished_event'].set()
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
                    if 'finished_event' in state:
                        state['finished_event'].set()
                    return True
                else:
                    mfaalog.info("[Callback] 任务正常结束")
                    # 正常结束不设置弹窗信息，避免被误判为异常
                    state['finished'] = True
                    if 'finished_event' in state:
                        state['finished_event'].set()
                    return True
            
            # 处理其他单按钮弹窗 (showwarning/showerror/showinfo 类型，BBC Server自动关闭)
            elif any(keyword in popup_title for keyword in ['正在结束任务', '其他任务运行中']):
                mfaalog.info(f"[Callback] 检测到提示弹窗: {popup_title}")

                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                if 'finished_event' in state:
                    state['finished_event'].set()
                mfaalog.info("[Callback] 提示弹窗已处理，战斗结束")
                return True

            # 处理未知弹窗（兜底）
            else:
                mfaalog.warning(f"[Callback] 未知弹窗: {popup_title}")
                # BBC Server 会自动关闭，标记为结束
                state['finished'] = True
                state['popup_title'] = popup_title
                state['popup_message'] = popup_message
                if 'finished_event' in state:
                    state['finished_event'].set()
                return True
        
        return False
    
    def _wait_for_battle_end(self, state: dict, manager):
        """
        阻塞等待战斗流程结束，通过定期 manager 心跳检测服务故障。

        参数:
            state (dict): 共享状态字典，包含键：
                - 'finished' (bool): 设为 True 以结束等待。
                - 'popup_title' (str|None): 发生终端弹窗时填充。
                - 'popup_message' (str|None): 发生终端弹窗时填充。
                - 'need_restart' (bool, 可选): 失败时可能设为请求 BBC 重启。
                - 'finished_event' (threading.Event): 弹窗处理器在 finished 变化时设置的事件。
            manager: BBC 连接管理器，用于通过 `send_command('get_status', ...)` 执行心跳检查。

        返回:
            tuple: (popup_title, popup_message) - 保存在 `state` 中的最终弹窗标题和消息。
        """

        # 主线程：只做心跳检查
        heartbeat_interval = 30  # 30秒一次心跳
        finished_event = state.get('finished_event')
        if finished_event is None:
            finished_event = threading.Event()
            state['finished_event'] = finished_event

        while not state['finished']:
            # Wait on event with timeout; if set early we wake immediately
            finished_event.wait(timeout=heartbeat_interval)

            if state['finished']:
                break

            # 心跳检查
            status = manager.send_command('get_status', {}, timeout=5)
            if not status.get('success'):
                mfaalog.warning("[ExecuteBbcTask] BBC服务无响应")
                state['finished'] = True
                state['popup_title'] = '错误'
                state['popup_message'] = 'BBC服务异常'
                state['need_restart'] = True  # 标记需要重启BBC
                finished_event.set()
                break

            mfaalog.debug("[ExecuteBbcTask] 心跳检查正常")

        return state['popup_title'], state['popup_message']
    