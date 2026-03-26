import json
import os
import time
import win32gui
import win32con
import win32api
import win32process
import subprocess
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
from maa.controller import Win32Controller
from maa.toolkit import Toolkit
from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum


def _parse_single_param(argv: CustomAction.RunArg) -> str:
    """解析单个参数值，去掉可能的引号"""
    param = argv.custom_action_param if argv.custom_action_param else ""
    param = param.strip()
    # 循环去除多层引号
    while len(param) >= 2:
        if (param.startswith('"') and param.endswith('"')):
            param = param[1:-1].strip()
        elif (param.startswith("'") and param.endswith("'")):
            param = param[1:-1].strip()
        else:
            break
    return param


# 全局变量存储BBC窗口句柄和控制器
_bbc_hwnd = None
_bbc_controller = None

# 固定BBC路径
BBC_PATH = "./BBC/BBchannel"


def _get_scripts_settings_path() -> str:
    return os.path.join(BBC_PATH, 'scripts_settings.json')


def _load_scripts_settings() -> dict:
    path = _get_scripts_settings_path()
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_scripts_settings(settings: dict) -> None:
    path = _get_scripts_settings_path()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ==================== Action 1: 设置BBC配置 ====================
@AgentServer.custom_action("setup_bbc_config")
class SetupBbcConfig(CustomAction):
    """设置BBC队伍配置 - 处理 bbc_team_config"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        print(f"[1/6] SetupBbcConfig: custom_action_param = {repr(argv.custom_action_param)}")
        
        bbc_team_config = _parse_single_param(argv)
        
        if not bbc_team_config:
            print("错误：未提供队伍配置文件路径")
            return CustomAction.RunResult(success=False)
        
        print(f"SetupBbcConfig: team_config={bbc_team_config}")
        
        settings_dir = os.path.join(BBC_PATH, 'settings')
        
        # 拼接固定路径
        team_config_path = os.path.join(settings_dir, bbc_team_config)
        
        if not os.path.exists(team_config_path):
            print(f"队伍配置文件不存在: {team_config_path}")
            return CustomAction.RunResult(success=False)
        
        with open(team_config_path, 'r', encoding='utf-8') as f:
            team_config = json.load(f)
        
        # 保存连接设置
        connect_settings = {}
        scripts_settings = _load_scripts_settings()
        for key in ["connectMode", "snapshotDevice", "operateDevice"]:
            if key in scripts_settings:
                connect_settings[key] = scripts_settings[key]
        
        # 替换配置并恢复连接设置
        scripts_settings = team_config
        scripts_settings.update(connect_settings)
        
        _save_scripts_settings(scripts_settings)
        print(f"SetupBbcConfig: 配置已保存")
        return True


# ==================== Action 2: 执行BBC任务（整合版）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC任务 - 整合运行次数、苹果类型、启动、初始化和监控"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> bool:
        global _bbc_hwnd, _bbc_controller
        
        # 从 Context 获取节点数据（包含 pipeline_override 合并后的值）
        node_data = context.get_node_data("执行BBC任务")
        print(f"[ExecuteBbcTask] node_data={node_data}")
        
        if not node_data:
            print(f"[ExecuteBbcTask] 错误：无法获取节点数据")
            return CustomAction.RunResult(success=False)
        
        # 从 attach 字段获取参数
        attach_data = node_data.get('attach', {})
        print(f"[ExecuteBbcTask] attach_data={attach_data}")
        run_count = attach_data.get('run_count')
        apple_type = attach_data.get('apple_type')
        support_order_mismatch = attach_data.get('support_order_mismatch', False)
        team_config_error = attach_data.get('team_config_error', False)
        
        if run_count is None or apple_type is None:
            print(f"[ExecuteBbcTask] 错误：参数不完整，run_count={run_count}, apple_type={apple_type}")
            return CustomAction.RunResult(success=False)
        
        run_count = int(run_count)
        print(f"[ExecuteBbcTask] run_count={run_count}, apple_type={apple_type}, support_order_mismatch={support_order_mismatch}, team_config_error={team_config_error}")
        
        print(f"[123456] run_count={run_count}, apple_type={apple_type}")
        
        # 2. 启动BBC进程
        bbc_exe_path = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
        if not os.path.exists(bbc_exe_path):
            print(f"BBC可执行文件不存在: {bbc_exe_path}")
            return CustomAction.RunResult(success=False)
        
        os.startfile(bbc_exe_path)
        print("[2/5] BBC进程已启动，等待窗口...")
        time.sleep(3)
        
        # 3. 查找并关闭免责声明窗口，确认已关闭
        print("[3/5] 查找并关闭免责声明窗口...")
        attempt = 0
        while True:
            attempt += 1
            disclaimer_hwnd = self._find_window_by_title("免责声明！")
            if disclaimer_hwnd:
                print(f"[3/5] 检测到免责声明窗口（尝试 {attempt}），正在关闭...")
                self._close_window_by_title("免责声明！")
                time.sleep(1)
            else:
                print("[3/5] 免责声明窗口已关闭")
                break
        
        # 4. 查找BBC窗口
        print("[4/5] 查找BBC窗口...")
        bbc_hwnd = None
        attempt = 0
        while True:
            attempt += 1
            bbc_hwnd = self._find_window_by_title("BBchannel")
            if bbc_hwnd:
                print(f"[4/5] BBC窗口已找到，hwnd={bbc_hwnd}（尝试 {attempt}）")
                break
            time.sleep(1)
        
        # 5. 执行刷本次数节点并监控战斗结束
        print("[5/5] 执行刷本次数节点并监控战斗结束...")
        if not self._execute_bbc_battle(context, bbc_hwnd, run_count, apple_type, support_order_mismatch, team_config_error):
            print("[5/5] 错误：BBC战斗执行失败")
            return CustomAction.RunResult(success=False)
        
        print("ExecuteBbcTask: 任务已完成")
        return True
    
    def _find_window_by_title(self, title):
        """根据标题查找窗口"""
        def callback(hwnd, extra):
            if win32gui.IsWindowVisible(hwnd):
                window_title = win32gui.GetWindowText(hwnd)
                if title in window_title:
                    extra.append(hwnd)
        
        matches = []
        win32gui.EnumWindows(callback, matches)
        return matches[0] if matches else None
    
    def _close_window_by_title(self, title):
        """根据标题关闭窗口"""
        hwnd = self._find_window_by_title(title)
        if hwnd:
            # 发送关闭消息
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            return True
        return False
    

    
    def _execute_bbc_battle(self, context, bbc_hwnd, run_count, apple_type, support_order_mismatch=False, team_config_error=False):
        """执行BBC战斗流程并监控结束"""
        try:
            from maa.controller import Win32Controller
            from maa.tasker import Tasker
            from maa.define import MaaWin32ScreencapMethodEnum, MaaWin32InputMethodEnum
            
            # 创建控制器实例，明确配置截图和输入方式
            controller = Win32Controller(
                bbc_hwnd,
                screencap_method=MaaWin32ScreencapMethodEnum.PrintWindow,
                mouse_method=MaaWin32InputMethodEnum.Seize,
                keyboard_method=MaaWin32InputMethodEnum.Seize
            )
            
            # 连接控制器
            controller.post_connection().wait()
            
            # 使用共享资源
            resource = context.tasker.resource if hasattr(context, 'tasker') and context.tasker else None
            if not resource:
                from maa.resource import Resource
                resource = Resource()
                # 加载资源（使用正确的资源路径）
                resource.post_bundle("./assets/resource").wait()
            
            # 创建 tasker 并绑定控制器
            tasker = Tasker()
            tasker.bind(resource, controller)
            
            # 执行任务 - 点击刷本次数，设置执行次数和选择苹果的节点
            # 直接使用 apple_type（已经是中文）
            selected_apple = apple_type if apple_type else "金苹果"  # 默认金苹果
            
            print(f"[ExecuteBbcTask] 选择的苹果类型: {selected_apple}")
            
            pipeline_override = {
                "输入运行次数": {
                    "action": {"type": "InputText", "param": {"input_text": str(run_count)}},
                    "next": [selected_apple, "[JumpBack]选苹果"]  # 根据用户选择的苹果类型动态变更 next 节点
                },
                "执行BBC任务": {
                    "run_count": run_count,
                    "apple_type": apple_type
                }
            }
            
            # 执行任务
            print(f"[ExecuteBbcTask] 执行 点击刷本次数 任务，pipeline_override: {pipeline_override}")
            result = tasker.post_task("点击刷本次数", pipeline_override).wait().succeeded
            print(f"[ExecuteBbcTask] 点击刷本次数 任务执行结果: {result}")
            
            # 阶段1：弹窗检测（只检测用户选择"继续"的弹窗）
            if support_order_mismatch:
                popup_node_map = {
                    "助战排序不符合": "助战排序不符合_继续"
                }
                
                # 每隔15秒检测一次，持续2次
                for i in range(2):
                    print(f"等待15秒后第{i+1}次弹窗检测...")
                    time.sleep(15)
                    print(f"开始第{i+1}次弹窗检测...")
                    popup_hwnd = self._find_window_by_title("助战排序不符合")
                    if popup_hwnd:
                        print(f"检测到弹窗: 助战排序不符合")
                        print(f"用户配置为继续，执行弹窗处理节点: 助战排序不符合")
                        result = tasker.post_task("助战排序不符合_继续").wait().succeeded
                        print(f"弹窗处理节点执行结果: {result}")
            
            # 阶段2：战斗结束检测
            print("开始监控BBC战斗结束...")
            battle_end_windows = ["脚本停止！", "正在结束任务！", "助战排序不符合"]
            # 如果用户未开启"忽视队伍配置错误"，则加入检测列表
            if not team_config_error:
                battle_end_windows.append("队伍配置错误！")
            
            while True:
                for window_title in battle_end_windows:
                    if self._find_window_by_title(window_title):
                        print(f"检测到战斗结束窗口: {window_title}")
                        # 强制关闭BBC
                        print("强制关闭BBC...")
                        try:
                            win32gui.PostMessage(bbc_hwnd, win32con.WM_CLOSE, 0, 0)
                            time.sleep(2)
                            if win32gui.IsWindow(bbc_hwnd):
                                print("优雅关闭失败，强制kill进程...")
                                _, pid = win32process.GetWindowThreadProcessId(bbc_hwnd)
                                subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
                        except Exception as e:
                            print(f"关闭BBC时出错: {e}")
                            try:
                                subprocess.run(['taskkill', '/F', '/IM', 'BBchannel.exe'], capture_output=True)
                            except:
                                pass
                        
                        # BBC关闭后再关闭控制器
                        print("关闭控制器连接...")
                        controller.post_inactive().wait()
                        return True
                
                # 如果用户开启了"忽视队伍配置错误"，检测到该弹窗时执行节点而不是关闭
                if team_config_error and self._find_window_by_title("队伍配置错误！"):
                    print("检测到弹窗: 队伍配置错误！")
                    print(f"用户配置为继续，执行弹窗处理节点: 队伍配置错误！")
                    result = tasker.post_task("队伍配置错误_继续").wait().succeeded
                    print(f"弹窗处理节点执行结果: {result}")
                    # 执行后继续检测
                    continue
                
                time.sleep(5)
        except Exception as e:
            print(f"执行BBC战斗流程出错: {e}")
            return False