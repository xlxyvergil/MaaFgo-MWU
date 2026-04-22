# ==================== BBchannel TCP Server ====================
# 纯 TCP 模式，用于 MaaFgo 通信
# 协议: 4字节长度(big-endian) + JSON数据
# 架构: 标准 CS 模式，服务器只执行操作，客户端控制流程

# ==================== 日志开关 ====================
ENABLE_LOG = True

import logging as _logging
import os as _os

_server_logger = _logging.getLogger("BbcTcpServer")
if ENABLE_LOG and not _server_logger.handlers:
    _server_logger.setLevel(_logging.DEBUG)
    _server_logger.propagate = False
    _log_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'bbc_server.log')
    _fh = _logging.FileHandler(_log_path, mode='w', encoding='utf-8')
    _fh.setFormatter(_logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    _server_logger.addHandler(_fh)

def _log(level, msg):
    """
    将消息记录到 stdout，并在启用时转发到模块日志器。

    参数:
        level (str): 日志级别，取值之一为 'debug'、'info'、'warning'、'error'。未知值在转发到日志器时被忽略。
        msg (str): 要发出的消息文本。

    注意:
        总是打印以 "[BBC-TCP]" 为前缀的消息。如果 ENABLE_LOG 为 False，消息不会转发到文件日志器。
    """
    print(f"[BBC-TCP] {msg}")
    import sys
    sys.stdout.flush()
    if not ENABLE_LOG:
        return
    if level == 'debug':
        _server_logger.debug(msg)
    elif level == 'info':
        _server_logger.info(msg)
    elif level == 'warning':
        _server_logger.warning(msg)
    elif level == 'error':
        _server_logger.error(msg)

# ==================== 全局状态 ====================
_bb_window_global = None
CT = None
Battle = None
popup_event_queue = None
_popup_wait_dict = {}
_popup_wait_lock = None

# Tkinter 线程安全队列（从 TCP 线程到主线程）
_tk_queue = None
_tk_root_ref = None  # Tkinter 主窗口引用，用于 after() 调用

TCP_PORT = 25001
CALLBACK_PORT = 25002

# ==================== BBC 窗口注册 ====================

def update_bb_window(bb_window):
    """
    注册应用的 Tkinter root/窗口供 TCP 服务器使用。

    将提供的 `bb_window` 注册为模块级 BB 窗口引用和用于在 Tkinter 主线程上调度 UI 工作的 Tk root；同时初始化 Tk 队列处理器。

    参数:
        bb_window: 要注册的主 Tkinter 窗口或 root 对象。
    """
    global _bb_window_global, _tk_root_ref
    _bb_window_global = bb_window
    _tk_root_ref = bb_window  # 保存主窗口引用用于 after() 调用
    _setup_tk_queue_handler()
    _log('info', '[Server] BBC window registered')

def get_bb_page():
    """
    从已注册的 BB 窗口返回主页面对象。

    返回:
        object: 存储的 BB 窗口的第一个页面对象（`_bb_window_global.pages[0]`），如果没有注册 BB 窗口则返回 `None`。
    """
    if _bb_window_global is None:
        return None
    return _bb_window_global.pages[0]

def _process_tk_queue(event=None):
    """
    执行并排空 Tkinter 主线程队列中排队的所有可调用对象。

    如果内部 Tk 队列未初始化则为空操作。此函数重复检索并调用队列中的每个可调用对象，直到队列为空。
    单个可调用对象引发的异常被捕获并记录；剩余项目的处理继续进行。

    参数:
        event (可选): 当此函数用作事件处理器时被忽略的 Tk 事件对象。
    """
    global _tk_queue
    if _tk_queue is None:
        return
    # Process all queued functions (non-blocking)
    while True:
        try:
            func = _tk_queue.get_nowait()
            try:
                func()
            except Exception as e:
                _log('error', f'[TkQueue] Error executing queued function: {e}')
        except __import__('queue').Empty:
            break


def _setup_tk_queue_handler():
    """
    确保 Tkinter 队列存在并注册一个在 Tk 主循环上处理队列中可调用对象的事件处理器。

    如果未注册 Tk root，则为空操作。创建模块队列（如果缺失），并尝试在注册的 Tk root 上绑定自定义 `<<TkQueueEvent>>` 事件到队列处理处理器；
    绑定失败被记录。
    """
    global _tk_root_ref, _tk_queue
    if _tk_root_ref is None:
        return
    if _tk_queue is None:
        _tk_queue = __import__('queue').Queue()
    # Bind the event handler to process queue on event
    try:
        _tk_root_ref.bind('<<TkQueueEvent>>', _process_tk_queue)
        _log('info', '[TkQueue] Event handler bound to <<TkQueueEvent>>')
    except Exception as e:
        _log('warning', f'[TkQueue] Failed to bind event handler: {e}')


def _run_on_tk_thread(func):
    """
    调度可调用对象到 Tkinter 主线程运行。

    将 `func` 放入内部 Tk 队列，如果注册了 Tk root 窗口，则通过生成自定义 `<<TkQueueEvent>>` 触发 root 处理队列。
    如果未注册 Tk root，则可调用对象保持排队直到提供 root。
    触发 Tk root 时引发的异常（例如窗口已关闭）被忽略。
    """
    global _tk_queue, _tk_root_ref
    if _tk_queue is None:
        _tk_queue = __import__('queue').Queue()
    _tk_queue.put(func)
    # 唤醒主线程处理队列（如果主窗口存在）
    if _tk_root_ref is not None:
        try:
            _tk_root_ref.event_generate('<<TkQueueEvent>>', when='tail')
        except Exception:
            pass  # 主窗口可能已关闭

def _run_on_tk_thread_and_wait(func, timeout=10):
    """
    调度可调用对象到 Tkinter 主线程运行，并同步等待其执行完成。

    适用于必须等待 UI 操作真正完成后再继续的场景（例如 load_config 中的 page.reset()）。

    参数:
        func: 要在 Tkinter 主线程上执行的可调用对象。
        timeout (float): 等待完成的超时秒数。

    返回:
        bool: 函数在超时内执行完成返回 True，否则返回 False。
    """
    import threading
    done_event = threading.Event()
    result = [None]
    exception = [None]

    def wrapper():
        try:
            func()
        except Exception as e:
            exception[0] = e
        finally:
            done_event.set()

    _run_on_tk_thread(wrapper)
    if not done_event.wait(timeout=timeout):
        _log('warning', f'[_run_on_tk_thread_and_wait] 等待超时: {func.__name__}')
        return False
    if exception[0]:
        _log('error', f'[_run_on_tk_thread_and_wait] 执行异常: {exception[0]}')
        return False
    return True

# ==================== 弹窗机制 ====================

def _remove_popup_from_queue(popup_id):
    """
    从全局 popup_event_queue 中移除与给定 popup_id 匹配的所有弹窗事件。

    如果队列为 None，则函数不执行任何操作。其他排队的弹窗事件被保留并按原始顺序重新排队。
    此函数是非阻塞的，如果队列为空或无法检索项目不会抛出异常。

    参数:
        popup_id (str): 要移除的弹窗事件的唯一标识符。
    """
    global popup_event_queue
    if popup_event_queue is None:
        return
    temp_list = []
    while not popup_event_queue.empty():
        try:
            p = popup_event_queue.get_nowait()
            if p['id'] != popup_id:
                temp_list.append(p)
        except:
            break
    for p in temp_list:
        popup_event_queue.put(p)

def _resolve_popup(popup_id, action):
    """
    通过记录提供的操作并将其标记为已解决来解析跟踪的弹窗。

    如果 popup_id 存在于内部等待字典中且其状态为 "waiting"，则将其 `result` 设为 `action`，`status` 设为 "resolved"，
    并用弹窗的 `title`、`message` 和提供的 `action` 更新模块级 `_last_resolved_popup`。

    参数:
        popup_id (str): 要解析的弹窗标识符。
        action:要与弹窗关联的解析值（例如 'ok'、'cancel'、True/False）。
    """
    global _last_resolved_popup
    with _popup_wait_lock:
        popup_info = _popup_wait_dict.get(popup_id)
        if popup_info and popup_info.get('status') == 'waiting':
            popup_info['result'] = action
            popup_info['status'] = 'resolved'
            _last_resolved_popup = {
                'title': popup_info.get('title', ''),
                'message': popup_info.get('message', ''),
                'result': action
            }

# ==================== 模块延迟导入 ====================

def ensure_imports():
    """
    确保可选模块和常量作为模块级全局变量可用。

    尝试从 `consts` 导入 `Consts`（赋给全局 `CT`）、从 `device` 导入 `Windows`、`LDdevice`、`Mumudevice`，
    并从 `FGObattle` 导入 `Battle`。
    如果 `consts.Consts` 无法导入，则用提供颜色常量和 `BATTLE_TYPE` 列表的最小 `MockCT` 填充 `CT`。
    """
    global CT, Battle, Windows, LDdevice, Mumudevice
    if CT is not None:
        return
    try:
        from consts import Consts as CT
    except:
        class MockCT:
            Gold = "gold"
            Silver = "silver"
            Copper = "copper"
            Blue = "blue"
            Colorful = "colorful"
            BATTLE_TYPE = ['连续出击(或强化本)', '自动编队爬塔(应用操作序列设置)']
        CT = MockCT()
    try:
        from device import Windows, LDdevice, Mumudevice
    except:
        pass
    try:
        from FGObattle import Battle
    except:
        pass

# ==================== API 实现类 ====================

class ConnectionAPI:
    @staticmethod
    def connect_mumu(path=None, index=0, pkg=None, app_index=0):
        """
        连接 MuMu 模拟器实例并将其注册为 BB 客户端的当前设备。

        参数:
            path (str | None): MuMu 安装目录；如果为 None，函数将尝试从 MuMuInstallPath.txt 读取保存的路径。
            index (int): 模拟器索引（0 表示默认实例）。
            pkg (str | None): 在模拟器上使用的 Android 包名；省略时默认为 "com.bilibili.fatego"。
            app_index (int): 传递给 Mumudevice 构造器的应用索引。

        返回:
            dict: 成功时返回 {'success': True}；失败时返回 {'success': False, 'error': '<message>'}。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not path:
            if os.path.exists("MuMuInstallPath.txt"):
                with open("MuMuInstallPath.txt", "r", encoding="utf8") as f:
                    path = f.read().strip()
        if not path:
            return {'success': False, 'error': 'MuMu path not specified'}
        try:
            path = Mumudevice.check_mumuInstallPath(path)
            with open("MuMuInstallPath.txt", "w", encoding="utf8") as f:
                f.write(path)
            Mumudevice.mumuPath = path
            emulator_name = f"MuMu模拟器12-{index}" if index > 0 else "MuMu模拟器12"
            pkg_name = pkg if pkg else "com.bilibili.fatego"
            device = Mumudevice(path, index, app_index, pkg_name, use_manager=True)
            serialno = {'name': emulator_name, 'pkg': pkg_name, 'appIndex': app_index}
            device.set_serialno(json.dumps(serialno, ensure_ascii=False))
            device.snapshot()
            page.snapshotDevice = page.operateDevice = page.device.snapshotDevice = page.device.operateDevice = device
            # 线程安全：将 UI 更新分发到主线程
            def update_ui():
                """
                刷新当前 BB 页面的标签及其连接列表以反映最新的设备和连接状态。

                更新活动页面的 pagebar 标签文本，并刷新已注册 BB 窗口中的连接列表 widget。
                """
                _bb_window_global.pagebar.tags[page.idx].createText(True)
                _bb_window_global.updateConnectLst(page.idx)
            _run_on_tk_thread(update_ui)
            _log('info', f'[Connection] MuMu connected: {emulator_name}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] MuMu connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def connect_ld(path=None, index=0):
        """
        连接到 LDPlayer 安装，将生成的设备附加到当前 BB 页面，并调度 UI 刷新。

        参数:
            path (str | None): LDPlayer 安装路径。如果省略，尝试从 "LDInstallPath.txt" 读取上次使用的路径。
            index (int): 用于标识实例并填充设备序列信息的模拟器索引。

        返回:
            dict: 成功时返回 {'success': True}；失败时返回 {'success': False, 'error': <message>}。

        注意:
            - 成功时验证的路径会持久化到 "LDInstallPath.txt"。
            - 创建的 LDdevice 被分配给页面的 snapshotDevice/operateDevice 槽，Windows touch wrapper 被分配给 operateDevice。
            - 在 Tkinter 主线程上调度 UI 更新以刷新页面标签和连接列表。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not path:
            if os.path.exists("LDInstallPath.txt"):
                with open("LDInstallPath.txt", "r", encoding="utf8") as f:
                    path = f.read().strip()
        if not path:
            return {'success': False, 'error': 'LD path not specified'}
        try:
            path = LDdevice.checkPath(path)
            with open("LDInstallPath.txt", "w", encoding="utf8") as f:
                f.write(path)
            device = LDdevice(path, index)
            serialno = {'name': str(index)}
            device.set_serialno(json.dumps(serialno, ensure_ascii=False))
            device.snapshot()
            from device import Windows
            touchDevice = Windows(device.player.bndWnd)
            page.snapshotDevice = page.device.snapshotDevice = device
            page.operateDevice = page.device.operateDevice = touchDevice
            # 线程安全：将 UI 更新分发到主线程
            def update_ui():
                """
                Refreshes the current BB page's tab label and its connection list to reflect the latest device and connection state.
                
                Updates the pagebar tab text for the active page and refreshes the connection list widget in the registered BB window.
                """
                _bb_window_global.pagebar.tags[page.idx].createText(True)
                _bb_window_global.updateConnectLst(page.idx)
            _run_on_tk_thread(update_ui)
            _log('info', f'[Connection] LD connected: index={index}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] LD connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def connect_adb(ip):
        """
        通过 ADB 连接到 Android 设备并将其注册为当前页面设备。

        验证本地 adb 可执行文件是否存在，运行 `adb connect {ip}`，创建 Android 设备实例并将其分配给页面的 snapshot/operate 设备字段，
        并调度 Tkinter 线程 UI 刷新以反映新连接。

        参数:
            ip (str): 要通过 ADB 连接的 Android 设备的 IP 地址（host[:port]）。

        返回:
            dict: 成功时返回 `{'success': True}`。
                  失败时返回 `{'success': False, 'error': <message>}`（例如 BBC 窗口未就绪、缺少 `ip`、ADB 未找到、设备不可用或其他连接错误）。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, sys
        if not ip:
            return {'success': False, 'error': 'IP not specified'}
        try:
            adb_path = os.path.join(os.path.dirname(sys.executable), "airtest", "core", "android", "static", "adb", "windows")
            if not os.path.exists(os.path.join(adb_path, "adb.exe")):
                return {'success': False, 'error': 'ADB not found'}
            from bbcmd import cmd
            cmd(f'"{adb_path}/adb" connect {ip}')
            from device import Android, USE_AS_BOTH
            server = page.SS.get('server', 'CH')
            device = Android(ip, server, USE_AS_BOTH, cap_method="Minicap")
            if not device.available:
                device.disconnect()
                return {'success': False, 'error': 'ADB device unavailable'}
            page.snapshotDevice = page.operateDevice = page.device.snapshotDevice = page.device.operateDevice = device
            # 线程安全：将 UI 更新分发到主线程
            def update_ui():
                """
                Refreshes the current BB page's tab label and its connection list to reflect the latest device and connection state.
                
                Updates the pagebar tab text for the active page and refreshes the connection list widget in the registered BB window.
                """
                _bb_window_global.pagebar.tags[page.idx].createText(True)
                _bb_window_global.updateConnectLst(page.idx)
            _run_on_tk_thread(update_ui)
            _log('info', f'[Connection] ADB connected: {ip}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] ADB connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def disconnect():
        """
        停止并断开 BB 窗口当前注册的设备。

        尝试将设备标记为不运行，如果设备有 `disconnect()` 方法则调用它。

        返回:
            dict: 成功停止/断开时返回 `{'success': True}`，或者如果 BB 窗口未就绪或发生错误则返回 `{'success': False, 'error': <message>}`。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        try:
            page.device.running = False
            if hasattr(page.device, 'disconnect'):
                page.device.disconnect()
            _log('info', '[Connection] Disconnected')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def get_connection():
        """
        从已注册的 BB 窗口获取当前设备连接和运行时元数据。

        返回:
            dict: 包含以下键的字典：
                - connected (bool): 设备是否已连接/可用。
                - available (bool): 与 `connected` 相同。
                - running (bool): 设备/任务是否正在运行。
                - task_name (str): 设备的当前任务名称。
                - device_type (str): 设备对象的运行时类名（错误时为 'Unknown'）。
                - device_info (dict): 详细设备信息，包含：
                    - serialno (str): 设备序列字符串。
                    - running (bool): 与顶层 `running` 相同。
                    - task_name (str): 与顶层 `task_name` 相同。
                    - emulator_params (dict): 尽力而为的模拟器特定参数：
                        - Mumudevice: `mumu_path`、`emulator_index`、`app_index`、`pkg`。
                        - LDdevice: `ld_path`、`emulator_index`。
                        - Android/ADB: `ip`（如果可能从 serial 解析）。
                    - player_hwnd (str, 可选): 播放器窗口句柄（如果有）。
                - error (str, 可选): 检索失败时的错误信息。
        """
        page = get_bb_page()
        if page is None:
            return {
                'connected': False,
                'available': False,
                'running': False,
                'task_name': '',
                'device_type': 'None',
                'device_info': {}
            }
        try:
            device_available = bool(page.device.available)
            device_running = bool(getattr(page.device, 'running', False))
            task_name = str(getattr(page.device, 'taskName', ''))

            device_type = type(page.device).__name__
            serialno_str = str(getattr(page.device, 'serialno', ''))

            # 获取模拟器连接参数（从实际设备对象获取）
            emulator_params = {}
            actual_device = getattr(page.device, 'snapshotDevice', None)
            if not actual_device:
                actual_device = getattr(page.device, 'operateDevice', None)
            if not actual_device:
                actual_device = page.device
            
            actual_device_type = type(actual_device).__name__ if actual_device else device_type
            
            if actual_device_type == 'Mumudevice':
                emulator_params = {
                    'mumu_path': getattr(actual_device, 'mumuPath', ''),
                    'emulator_index': getattr(actual_device, 'emulatorIndex', 0),
                    'app_index': getattr(actual_device, 'appIndex', 0),
                    'pkg': getattr(actual_device, 'pkg', '')
                }
            elif actual_device_type == 'LDdevice':
                emulator_params = {
                    'ld_path': getattr(actual_device, 'ldPath', ''),
                    'emulator_index': getattr(actual_device, 'emulatorIndex', 0)
                }
            elif actual_device_type == 'Android':
                # ADB 连接，从 serialno 解析 IP
                import json
                try:
                    serialno_data = json.loads(serialno_str) if serialno_str else {}
                    emulator_params = {
                        'ip': serialno_data.get('host', '') if isinstance(serialno_data, dict) else serialno_str
                    }
                except:
                    emulator_params = {'ip': serialno_str}

            device_info = {
                'serialno': serialno_str,
                'running': device_running,
                'task_name': task_name,
                'emulator_params': emulator_params
            }

            try:
                if hasattr(page.device, 'player') and page.device.player:
                    device_info['player_hwnd'] = str(getattr(page.device.player, 'bndWnd', 'N/A'))
            except:
                pass

            return {
                'connected': device_available,
                'available': device_available,
                'running': device_running,
                'task_name': task_name,
                'device_type': device_type,
                'device_info': device_info
            }
        except Exception as e:
            return {
                'connected': False,
                'available': False,
                'running': False,
                'task_name': '',
                'device_type': 'Unknown',
                'device_info': {},
                'error': str(e)
            }


class ConfigAPI:
    @staticmethod
    def load_config(filename):
        """
        从设置目录加载配置 JSON 并应用到已注册 BB 窗口状态。

        替换页面设置时保留当前页面的 `connectMode`、`snapshotDevice` 和 `operateDevice` 键，
        在 Tkinter 线程上调度 UI 重置，并通过全局 BB 窗口保存程序持久化设置。

        参数:
            filename (str): 位于 `settings` 目录下的配置文件基本名。不得包含路径遍历序列（`..`、`/` 或 `\`）。

        返回:
            dict: 操作结果。成功时：`{'success': True}`。失败时：`{'success': False, 'error': <message>}` 描述原因（例如文件未找到、无效 JSON、BBC 窗口未就绪）。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not filename:
            return {'success': False, 'error': 'filename required'}

        # 路径安全检查：防止 ../ 逃逸
        if '..' in filename or '/' in filename or '\\' in filename:
            _log('error', f'[Config] Invalid filename (path traversal detected): {filename}')
            return {'success': False, 'error': 'Invalid filename: path traversal not allowed'}

        # BBC 进程的工作目录就是 BBchannel 根目录，直接使用相对路径
        config_path = os.path.join("settings", filename)
        
        if not os.path.exists(config_path):
            return {'success': False, 'error': f'Config file not found: {config_path}'}
        try:
            with open(config_path, "r", encoding="utf8") as fp:
                SS = json.load(fp)
            SS["connectMode"] = page.SS.get("connectMode", None)
            SS["snapshotDevice"] = page.SS.get("snapshotDevice", None)
            SS["operateDevice"] = page.SS.get("operateDevice", None)
            page.SS = SS
            # 线程安全：page.reset() 必须真正完成后再返回，否则后续的 set_apple_type 等会覆盖刚加载的值
            if not _run_on_tk_thread_and_wait(page.reset, timeout=10):
                return {'success': False, 'error': 'page.reset() 执行超时'}
            _bb_window_global.saveJsons()
            _log('info', f'[Config] Loaded: {filename}')
            return {'success': True}
        except json.JSONDecodeError as e:
            _log('error', f'[Config] JSON parse error in {filename}: {e}')
            return {'success': False, 'error': f'Invalid JSON format: {str(e)}'}
        except Exception as e:
            _log('error', f'[Config] Load failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def save_config(filename):
        """
        将当前 BB 频道配置保存到 settings 目录下的文件中。

        参数:
            filename (str): 要写入 "settings" 目录内的文件名。必须是简单文件名（不能包含 ".."、"." 或 "\" 以防止路径遍历）。

        返回:
            dict: 成功时返回 `{'success': True}`；失败时返回 `{'success': False, 'error': <message>}` 描述问题。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not filename:
            return {'success': False, 'error': 'filename required'}
        
        # 路径安全检查：防止 ../ 逃逸
        if '..' in filename or '/' in filename or '\\' in filename:
            _log('error', f'[Config] Invalid filename (path traversal detected): {filename}')
            return {'success': False, 'error': 'Invalid filename: path traversal not allowed'}
        
        # BBC 进程的工作目录就是 BBchannel 根目录，直接使用相对路径（与 BBchannelUI.py 一致）
        config_path = os.path.join("settings", filename)
        
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf8") as fp:
                json.dump(page.SS, fp, ensure_ascii=False, indent=4)
            _log('info', f'[Config] Saved: {filename}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Config] Save failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def get_config():
        """
        获取当前加载的 BB 窗口配置。

        返回:
            dict: 如果 BB 窗口已就绪，返回 `{'success': True, 'config': page.SS}`，其中 `config` 是设置字典；否则返回 `{'success': False, 'error': <message>}`。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        return {
            'success': True,
            'config': page.SS
        }


class BattleSettingsAPI:
    APPLE_MAP = {
        "gold": "gold",
        "silver": "silver",
        "blue": "blue",
        "copper": "copper",
        "colorful": "colorful"
    }

    BATTLE_TYPE_MAP = {
        "continuous": 0,
        "tower": 1,
        "连续出击": 0,
        "自动编队爬塔": 1,
        "连续出击(或强化本)": 0,
        "自动编队爬塔(应用操作序列设置)": 1,
    }

    @staticmethod
    def set_apple_type(apple_type):
        """
        设置战斗自动化的苹果类型并更新 UI 图标。

        参数:
            apple_type (str): BattleSettingsAPI.APPLE_MAP 的键（例如 "gold"、"silver"、"blue"、"copper"、"colorful"）。

        返回:
            dict: 成功时返回 `{'success': True, 'apple_type': <apple_type>}`。
                  如果 BBC 窗口未注册：`{'success': False, 'error': 'BBC window not ready'}`。
                  如果 `apple_type` 未知：`{'success': False, 'error': 'Unknown apple type: <apple_type>'}`。
                  如果苹果类型已应用但 UI 更新失败：`{'success': True, 'apple_type': <apple_type>, 'warning': <error message>}`。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        if apple_type not in BattleSettingsAPI.APPLE_MAP:
            return {'success': False, 'error': f'Unknown apple type: {apple_type}'}
        try:
            page.appleSet.appleType = CT.Gold if apple_type == "gold" else getattr(CT, apple_type.capitalize(), CT.Gold)
            # 线程安全：getAppleIconPhoto() 创建 PhotoImage，config() 更新 widget
            def update_apple_icon():
                """
                Refresh the apple icon PhotoImage on the page and update the widget if present.
                
                Updates page.appleSet.appleIconPhoto by calling getAppleIconPhoto(). If page.appleSet.appleIcon exists, applies the new PhotoImage to that widget.
                """
                page.appleSet.appleIconPhoto = page.appleSet.getAppleIconPhoto()
                if hasattr(page.appleSet, 'appleIcon'):
                    page.appleSet.appleIcon.config(image=page.appleSet.appleIconPhoto)
            _run_on_tk_thread(update_apple_icon)
            _log('info', f'[Battle] Apple type set: {apple_type}')
            return {'success': True, 'apple_type': apple_type}
        except Exception as e:
            _log('warning', f'[Battle] Apple type set but UI update failed: {e}')
            return {'success': True, 'apple_type': apple_type, 'warning': str(e)}

    @staticmethod
    def set_run_times(times):
        """
        设置 BB 窗口苹果设置的战斗迭代次数。

        参数:
            times (int): 期望的运行次数；必须是大于或等于 0 的整数。

        返回:
            dict: 成功时返回 `{'success': True, 'times': <int>}`。失败时返回 `{'success': False, 'error': <str>}` 描述未应用值的原因（例如 BB 窗口未就绪、无效值或内部错误）。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        if times is None or times < 0:
            return {'success': False, 'error': 'Invalid times value'}
        try:
            page.appleSet.runTimes.set(times)
            _log('info', f'[Battle] Run times set: {times}')
            return {'success': True, 'times': times}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def set_battle_type(battle_type):
        """
        在已注册 BB 窗口的设置中设置战斗类型。

        参数:
            battle_type (str): 标识所需战斗模式的键（必须存在于 BattleSettingsAPI.BATTLE_TYPE_MAP 中）。

        返回:
            dict: 成功时返回 `{'success': True, 'battle_type': <battle_type>}`；失败时返回 `{'success': False, 'error': '<message>'}`。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        if battle_type not in BattleSettingsAPI.BATTLE_TYPE_MAP:
            return {'success': False, 'error': f'Unknown battle type: {battle_type}'}
        try:
            idx = BattleSettingsAPI.BATTLE_TYPE_MAP[battle_type]
            page.battletype.set(CT.BATTLE_TYPE[idx])
            _log('info', f'[Battle] Battle type set: {battle_type}')
            return {'success': True, 'battle_type': battle_type}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def get_settings():
        """
        从已注册 BB 窗口返回当前战斗设置。

        返回:
            dict: 成功时返回 `{'success': True, 'apple_type': <CT color constant>, 'run_times': <int>, 'battle_type': <str>}`。
                  失败时返回 `{'success': False, 'error': <error message>}`（当 BB 窗口未就绪时也返回）。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        try:
            return {
                'success': True,
                'apple_type': page.appleSet.appleType,
                'run_times': page.appleSet.runTimes.get(),
                'battle_type': page.battletype.get()
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}


class BattleControlAPI:
    @staticmethod
    def start_battle():
        """
        通过程序点击 UI 启动按钮来发起战斗。

        验证 BB 窗口和 Battle 模块可用且所有三个从者槽都已填充；在 Tkinter 主线程上调度点击启动按钮。

        返回:
            dict: 成功时返回 `{'success': True}`，或者当服务器、战斗模块或从者验证失败或发生异常时返回 `{'success': False, 'error': <message>}`。
        """
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        if Battle is None:
            return {'success': False, 'error': 'Battle module not available'}
        try:
            for i in range(3):
                if not page.servantGroup[i].exist:
                    return {'success': False, 'error': f'Servant slot {i} is empty'}
            # 线程安全：event_generate() 必须在主线程调用
            def click_start_button():
                """
                模拟鼠标点击全局 BB 页面启动按钮中心。

                在生成点击前设置 page.firstRun = False 以绕过首次运行助手设置对话框（否则会阻止自动化执行）。
                需要全局 BB 页面及其 `start` widget 存在；入队按钮按下事件到 widget 中心。
                """
                # 跳过首次运行向导，避免弹出助战设置确认窗口
                page.firstRun = False
                btn_x = page.start.winfo_width() // 2
                btn_y = page.start.winfo_height() // 2
                page.start.event_generate("<Button-1>", x=btn_x, y=btn_y)
            _run_on_tk_thread(click_start_button)
            _log('info', '[Battle] Battle started')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Battle] Start failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def stop_battle():
        """
        停止已注册 BB 窗口上当前运行的战斗。

        如果有带运行设备的 BB 窗口可用，调用设备的 stop 操作。完成时返回成功字典，或 BBC 窗口未注册或发生异常时返回错误字典。

        返回:
            dict: 成功时返回 `{'success': True}`；如果 BBC 窗口未就绪或发生错误则返回 `{'success': False, 'error': <message>}`。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        try:
            if page.device.running:
                page.device.stop()
            _log('info', '[Battle] Battle stopped')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def pause_battle():
        """
        请求暂停当前运行的战斗（暂停未实现）。

        返回:
            dict: 包含 `success` 设为 False 和 `error` 消息的字典：
                - 如果 BBC 窗口未注册，返回 `'BBC window not ready'`。
                - 否则返回 `'Pause not implemented'`。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        return {'success': False, 'error': 'Pause not implemented'}

    @staticmethod
    def resume_battle():
        """
        恢复之前暂停的战斗。

        如果 BB 窗口未注册，返回指示窗口未就绪的错误。如果恢复未实现，返回明确未实现的错误。

        返回:
            dict: 当 BB 窗口未注册时返回 `{'success': False, 'error': 'BBC window not ready'}`，
                  当恢复功能不可用时返回 `{'success': False, 'error': 'Resume not implemented'}`。
        """
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        return {'success': False, 'error': 'Resume not implemented'}


class StatusAPI:
    @staticmethod
    def get_status():
        """
        收集已注册 BB 窗口的当前 UI 和设备状态。

        返回:
            dict: 包含以下键的状态字典：
                - 'success' (bool): 正常状态响应为 True。
                - 'ready' (bool): BB 页面已注册且状态已收集时为 True，否则为 False。
                - 'device_available' (bool): 当前设备是否标记为可用。
                - 'device_running' (bool): 设备当前是否正在运行。
                - 'task_name' (str): 当前设备任务名称（如果没有则为空字符串）。
                - 'device_type' (str): 设备运行时类名；当没有页面注册时为 'None'。
                - 'device_info' (dict): 设备详细信息，包含：
                    - 'serialno' (str): 设备序列标识符。
                    - 'running' (bool): 反映 'device_running'。
                    - 'task_name' (str): 反映 'task_name'。
                    - 'player_hwnd' (str, 可选): 设备播放器窗口句柄（如果有）。
                - 'battle_settings' (dict): 尽力而为的战斗 UI 设置；可能包含 'apple_type' (str)、'run_times' (int) 和 'battle_type' (str)。
                - 'popup_queue_size' (int): 待处理弹窗事件数量（如果弹窗队列未初始化则为 0）。
                - 'error' (str, 可选): 当 'ready' 为 False 且由于异常时存在；包含错误信息。
        """
        page = get_bb_page()
        if page is None:
            return {
                'success': True,
                'ready': False,
                'device_available': False,
                'device_running': False,
                'task_name': '',
                'device_type': 'None',
                'device_info': {},
                'battle_settings': {},
                'popup_queue_size': 0
            }
        try:
            device_available = bool(page.device.available)
            device_running = bool(getattr(page.device, 'running', False))
            task_name = str(getattr(page.device, 'taskName', ''))
            device_type = type(page.device).__name__
            serialno_str = str(getattr(page.device, 'serialno', ''))

            battle_settings = {}
            try:
                battle_settings = {
                    'apple_type': str(page.appleSet.appleType),
                    'run_times': page.appleSet.runTimes.get(),
                    'battle_type': str(page.battletype.get())
                }
            except:
                pass

            device_info = {
                'serialno': serialno_str,
                'running': device_running,
                'task_name': task_name
            }

            try:
                if hasattr(page.device, 'player') and page.device.player:
                    device_info['player_hwnd'] = str(getattr(page.device.player, 'bndWnd', 'N/A'))
            except:
                pass

            return {
                'success': True,
                'ready': True,
                'device_available': device_available,
                'device_running': device_running,
                'task_name': task_name,
                'device_type': device_type,
                'device_info': device_info,
                'battle_settings': battle_settings,
                'popup_queue_size': popup_event_queue.qsize() if popup_event_queue else 0
            }
        except Exception as e:
            return {'success': True, 'ready': False, 'error': str(e)}

    @staticmethod
    def get_popups():
        """
        获取跟踪的弹窗事件摘要而不修改内部弹窗队列。

        扫描模块弹窗事件队列，收集其 `id` 在 popup-wait 字典中的条目，然后恢复队列到原始内容。

        返回:
            dict: `{'success': True, 'popups': [...]}`，其中 `popups` 是对象列表，每个包含 `id`、`title`、`message` 和 `type`（默认为 `'unknown'`）。
        """
        if popup_event_queue is None:
            return {'success': True, 'popups': []}
        popups = []
        temp_list = []
        while not popup_event_queue.empty():
            try:
                p = popup_event_queue.get_nowait()
                temp_list.append(p)
                with _popup_wait_lock:
                    is_waiting = p['id'] in _popup_wait_dict
                if is_waiting:
                    popups.append({
                        'id': p['id'],
                        'title': p['title'],
                        'message': p['message'],
                        'type': p.get('popup_type', 'unknown')
                    })
            except:
                break
        for p in temp_list:
            popup_event_queue.put(p)
        return {'success': True, 'popups': popups}

    @staticmethod
    def popup_response(popup_id, action):
        """
        记录弹窗的响应并将其标记为已解决。

        参数:
            popup_id (str|int): 要解析的弹窗标识符。
            action (str): 为弹窗选择的操作（例如 "ok"、"cancel"、"yes"、"no"）。

        返回:
            dict: 找到弹窗并标记为已解决时返回 `{'success': True}`，否则返回 `{'success': False, 'error': 'Popup not found or already resolved'}`。
        """
        with _popup_wait_lock:
            popup_info = _popup_wait_dict.get(popup_id)
            if popup_info and popup_info.get('status') == 'waiting':
                popup_info['result'] = action
                popup_info['status'] = 'resolved'
                _log('info', f'[Popup] Response: id={popup_id}, action={action}')
                return {'success': True}
            return {'success': False, 'error': 'Popup not found or already resolved'}

    @staticmethod
    def wait_for_popup(timeout=30):
        """
        阻塞直到弹窗出现在内部弹窗事件队列中或超时。

        参数:
            timeout (int | float): 等待弹窗的最大秒数。

        返回:
            dict: 在超时前检测到弹窗则返回 `{'success': True, 'has_popup': True}`，否则返回 `{'success': True, 'has_popup': False}`。
        """
        import time
        start = time.time()
        while time.time() - start < timeout:
            if popup_event_queue and not popup_event_queue.empty():
                return {'success': True, 'has_popup': True}
            time.sleep(0.5)
        return {'success': True, 'has_popup': False}

    @staticmethod
    def get_ui_status():
        """
        获取当前 UI 状态和运行标志；在 Tkinter 主线程上读取顶部标签文本。

        通过 Tk 线程（如果有）读取窗口顶部标签文本，并报告设备和战斗运行状态。

        返回:
            dict: 包含以下键的字典：
                - 'success' (bool): 状态被收集时为 `True`（仅对意外失败才为 `False`）。
                - 'top_label' (str): UI 顶部标签的当前文本（如果不可用则为空字符串）。
                - 'device_running' (bool): 设备当前是否正在运行。
                - 'battle_running' (bool): 战斗是否正在运行（在此上下文中与 `device_running` 相同）。
                - 'error' (str, 可选): 仅在发生异常时存在。
        """
        page = get_bb_page()
        if page is None:
            return {
                'success': True,
                'top_label': '',
                'device_running': False,
                'battle_running': False
            }
        try:
            # 使用 Event 同步主线程执行
            import threading
            result = {'text': ''}
            event = threading.Event()

            def read_top_label():
                """
                Set result['text'] to the current text of the page top label and signal completion.
                
                If a `page.topLabel` widget exists, attempts to read its `'text'` option into `result['text']`. If the widget is missing or an error occurs while reading, `result` is left unchanged. Always calls `event.set()` to signal completion.
                """
                if hasattr(page, 'topLabel'):
                    try:
                        result['text'] = page.topLabel.cget('text')
                    except:
                        pass
                event.set()

            _run_on_tk_thread(read_top_label)
            # 等待主线程执行完成（超时0.5秒）
            event.wait(timeout=0.5)
            top_label_text = result['text']

            device_running = bool(getattr(page.device, 'running', False))

            return {
                'success': True,
                'top_label': top_label_text,
                'device_running': device_running,
                'battle_running': device_running  # BBC中device.running即表示战斗运行中
            }
        except Exception as e:
            return {
                'success': True,
                'top_label': '',
                'device_running': False,
                'battle_running': False,
                'error': str(e)
            }


# ==================== 命令分发器 ====================

class CommandDispatcher:
    HANDLERS = {
        'connect_mumu': ConnectionAPI.connect_mumu,
        'connect_ld': ConnectionAPI.connect_ld,
        'connect_adb': ConnectionAPI.connect_adb,
        'disconnect': ConnectionAPI.disconnect,
        'get_connection': ConnectionAPI.get_connection,
        'load_config': ConfigAPI.load_config,
        'save_config': ConfigAPI.save_config,
        'get_config': ConfigAPI.get_config,
        'set_apple_type': BattleSettingsAPI.set_apple_type,
        'set_run_times': BattleSettingsAPI.set_run_times,
        'set_battle_type': BattleSettingsAPI.set_battle_type,
        'get_settings': BattleSettingsAPI.get_settings,
        'start_battle': BattleControlAPI.start_battle,
        'stop_battle': BattleControlAPI.stop_battle,
        'pause_battle': BattleControlAPI.pause_battle,
        'resume_battle': BattleControlAPI.resume_battle,
        'get_status': StatusAPI.get_status,
        'get_ui_status': StatusAPI.get_ui_status,
        'get_popups': StatusAPI.get_popups,
        'popup_response': StatusAPI.popup_response,
        'wait_for_popup': StatusAPI.wait_for_popup,
    }

    @classmethod
    def dispatch(cls, cmd):
        """
        将封装的命令分发给注册的处理器并返回处理器结果。

        接受命令字典（或包含字典的单元素列表）。提取 'cmd' 作为处理器名称，'args' 作为关键字参数。
        如果命名的处理器已注册，当其签名没有参数时以无参数调用，否则以提供的 `args` 调用。
        对于无效格式、未知命令或处理器异常返回标准化错误字典。

        参数:
            cmd (dict | list): 要分发的命令。期望形状：`{'cmd': <handler_name>, 'args': { ... }}`。也接受包含此类字典的单元素列表。

        返回:
            dict: 成功时返回处理器的返回映射，失败时返回 `{'success': False, 'error': <message>}`。
        """
        if isinstance(cmd, list):
            cmd = cmd[0] if cmd else {}
        if not isinstance(cmd, dict):
            return {'success': False, 'error': f'Invalid command format: {type(cmd)}'}
        command = cmd.get('cmd', '')
        args = cmd.get('args', {})
        if not isinstance(args, dict):
            args = {}
        handler = cls.HANDLERS.get(command)
        if handler is None:
            return {'success': False, 'error': f'Unknown command: {command}'}
        try:
            import inspect
            sig = inspect.signature(handler)
            params = list(sig.parameters.keys())
            if len(params) == 0:
                return handler()
            else:
                return handler(**args)
        except Exception as e:
            _log('error', f'[Command] {command} failed: {e}')
            return {'success': False, 'error': str(e)}


# ==================== TCP 服务器 ====================

class ClientHandler:
    def __init__(self, client_socket, addr, server):
        """
        使用底层 socket、远程地址和所属服务器初始化 ClientHandler。

        参数:
            client_socket (socket.socket): 用于帧式 JSON I/O 的已连接客户端 socket。
            addr (tuple): socket.accept() 返回的远程地址元组，通常为 (host, port)。
            server (BBCServer): 用于客户端注册和协调的所属服务器实例。

        副作用:
            设置实例属性 `client`、`addr`、`server`，并启用 `running` 标志。
        """
        self.client = client_socket
        self.addr = addr
        self.server = server
        self.running = True

    def handle(self):
        """
        处理单个客户端连接：读取 4 字节大端长度前缀的 JSON 命令，分发它们，并发送长度前缀的 JSON 响应。

        读取帧，其中前 4 字节编码 payload 长度（大端），后跟 UTF-8 JSON payload。
        验证长度（必须 >0 且 <= 65535），解析 JSON 命令，通过命令分发器分发，并将分发结果作为帧式 UTF-8 JSON 响应返回。
        JSON 解析或分发错误时，客户端收到错误响应。
        记录连接、命令、响应和错误事件。
        确保处理器退出时客户端已取消注册且 socket 已关闭。
        """
        _log('info', f'[Client] Connected: {self.addr}')
        self.server.add_client(self)
        try:
            while self.running:
                len_bytes = self._recv_exact(4)
                if not len_bytes or len(len_bytes) < 4:
                    break
                msg_len = int.from_bytes(len_bytes, 'big')
                if msg_len > 65535 or msg_len <= 0:
                    break
                data = self._recv_exact(msg_len)
                if not data:
                    break
                try:
                    import json
                    cmd = json.loads(data.decode('utf-8'))
                    _log('debug', f'[Command] {cmd.get("cmd") if isinstance(cmd, dict) else cmd}')
                    response = CommandDispatcher.dispatch(cmd)
                except Exception as e:
                    _log('error', f'[Command] Parse failed: {e}')
                    response = {'success': False, 'error': str(e)}
                try:
                    import json
                    resp_data = json.dumps(response, ensure_ascii=False).encode('utf-8')
                    self.client.sendall(len(resp_data).to_bytes(4, 'big') + resp_data)
                    resp_str = json.dumps(response, ensure_ascii=False)
                    _log('debug', f'[Response] {resp_str}')
                except Exception as e:
                    _log('error', f'[Response] Send failed: {e}')
        except Exception as e:
            _log('error', f'[Client] Error: {e}')
        finally:
            self.server.remove_client(self)
            try:
                self.client.close()
            except:
                pass
            _log('info', f'[Client] Disconnected: {self.addr}')

    def _recv_exact(self, n):
        """
        从客户端 socket 精确读取 n 字节。

        参数:
            n (int): 要读取的字节数。

        返回:
            bytes: 长度为 `n` 的字节对象，包含接收到的数据；如果对端在读取 `n` 字节前关闭连接则返回 `b''`。
        """
        data = b''
        while len(data) < n:
            chunk = self.client.recv(n - len(data))
            if not chunk:
                return b''
            data += chunk
        return data

    def stop(self):
        """
        信号处理器停止处理并终止其运行循环。

        这将内部 running 标志设为 False，以便处理器的活动循环可以观察变化并退出。
        """
        self.running = False


class BBCServer:
    def __init__(self, port=25001):
        """
        创建配置为监听给定本地 TCP 端口的 BBCServer 并准备内部客户端状态。

        参数:
            port (int): 服务器绑定的 TCP 端口（默认：25001）。

        初始化的属性:
            port: 配置的监听端口。
            socket: 服务器监听 socket（直到 start() 前为 None）。
            running: 指示服务器循环是否活动的布尔标志。
            clients: 已连接 ClientHandler 实例列表。
            clients_lock: 保护 `clients` 访问的 threading.Lock。
        """
        self.port = port
        self.socket = None
        self.running = False
        self.clients = []
        self.clients_lock = __import__('threading').Lock()

    def add_client(self, client):
        """
        将已连接客户端注册到服务器。

        通过获取服务器的 clients 锁以线程安全的方式将 `client` 添加到服务器内部客户端列表。

        参数:
            client: 要注册的客户端处理器实例。
        """
        with self.clients_lock:
            self.clients.append(client)

    def remove_client(self, client):
        """
        如果客户端当前在列表中则将其从服务器的跟踪客户端列表中移除。

        参数:
            client: 要从服务器 clients 中移除的 ClientHandler 实例（或客户端标识符）。
        """
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)

    def start(self):
        """
        启动绑定到 127.0.0.1 实例配置端口的 TCP 服务器并接受传入的客户端连接。

        设置监听 socket（SO_REUSEADDR）绑定到回环接口，标记服务器为运行中，记录启动，然后进入接受连接的循环。
        对于每个接受的客户端，创建一个 ClientHandler 并在守护线程中运行。
        当服务器运行时，接受错误时记录错误并退出循环。

        副作用:
            - 将 `self.socket` 分配给创建的监听 socket
            - 设置 `self.running = True`
        """
        import socket
        import threading
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('127.0.0.1', self.port))
        self.socket.listen(5)
        self.running = True
        print(f"[TCP-Server] Started on 127.0.0.1:{self.port}")
        _log('info', f'[Server] BBC TCP Server started on 127.0.0.1:{self.port}')
        while self.running:
            try:
                client, addr = self.socket.accept()
                handler = ClientHandler(client, addr, self)
                threading.Thread(target=handler.handle, daemon=True).start()
            except Exception as e:
                if self.running:
                    _log('error', f'[Server] Accept error: {e}')
                break

    def stop(self):
        """
        停止服务器并关闭其监听 socket。

        将服务器的 running 标志设为 False 并关闭监听 socket（如果存在）；socket 关闭错误被忽略。
        """
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass


# ==================== 启动入口 ====================

_tcp_server_instance = None

def start_tcp_server(bb_window, port=25001):
    """
    注册 Tkinter BB 窗口用于远程控制，拦截受控 messagebox，并启动本地 TCP 服务器。

    将模块全局变量设置为引用提供的 BB Tk 窗口，用将"受控"弹窗加入队列并协调外部解决的包装器替换六个 tkinter.messagebox 函数，
    并启动绑定到 127.0.0.1:port 的后台 TCP 服务器。
    此函数执行就地副作用，并为服务器和可选的回调通知生成守护线程。

    参数:
        bb_window: 要注册用于 UI 调度和弹窗拦截的主 BB 应用程序 Tkinter 窗口（Tk 或 Toplevel）。
        port (int): 要监听的 TCP 端口（默认 25001）。
    """
    import threading
    import queue
    from tkinter import messagebox

    global _bb_window_global
    global popup_event_queue
    global _popup_wait_lock
    global _popup_wait_dict
    global _tcp_server_instance

    # Register BB window and set up Tk queue handler before any UI operations
    update_bb_window(bb_window)
    popup_event_queue = queue.Queue()
    _popup_wait_lock = threading.Lock()

    CONTROLLED_POPUPS = [
        "免责声明！",
        "自动连接失败",
        "助战排序不符合",
        "队伍配置错误！",
        "正在结束任务！",
        "脚本停止！",
        "其他任务运行中",
        "自动关机中！"
    ]

    original_messagebox = {
        'showinfo': messagebox.showinfo,
        'showwarning': messagebox.showwarning,
        'showerror': messagebox.showerror,
        'askokcancel': messagebox.askokcancel,
        'askyesno': messagebox.askyesno,
        'askretrycancel': messagebox.askretrycancel
    }

    def fix_encoding(s):
        """
        使用 UTF-8（带 GBK 回退）将输入值规范化为 Unicode 字符串。

        如果 `s` 是 `bytes`，则使用替换字符解码为 UTF-8。
        如果 `s` 是 `str`，则重新编码为 Latin-1 并解码为 GBK 以恢复可能误解码的字符。
        失败时返回原始输入不变。

        参数:
            s (bytes | str | any): 要规范化的值。

        返回:
            str: 解码成功时返回解码后的 Unicode 字符串；否则返回原始输入。
        """
        if isinstance(s, bytes):
            return s.decode('utf-8', errors='replace')
        try:
            return s.encode('latin-1').decode('gbk', errors='replace')
        except:
            return s

    def create_popup_wrapper(func_name, original_func):
        """
        创建拦截和管理"受控"弹窗的 messagebox 包装器。

        当对话框标题包含 CONTROLLED_POPUPS 中的任何关键字时，包装器：
        - 在 _popup_wait_dict 中注册弹窗并在 popup_event_queue 中加入弹窗事件，
        - 可选地向本地 CALLBACK_PORT 发送 `popup_show` 事件通知，
        - 可自动解决特定对话框（例如免责声明或单按钮警报），
        - 将显示和受控关闭行为委托给 _create_controlled_dialog 并返回其结果。

        参数:
            func_name (str): 被包装的 messagebox 函数名称（例如 'showinfo'、'askyesno'）。
            original_func (callable): 对于非受控对话框调用原始 tkinter.messagebox 函数，并用于显示受控对话框。

        返回:
            callable: 具有签名 (title, message, **kwargs) 的包装函数，它调用原始 messagebox 或管理和返回受控弹窗的结果。
        """
        def wrapper(title, message, **kwargs):
            """
            拦截标题包含任何配置受控关键字的 tkinter.messagebox 调用，并将其路由到模块的弹窗控制系统。

            当标题不匹配受控关键字时，直接委托给原始 messagebox 函数。
            对于受控弹窗，此函数：
            - 注册弹窗条目并入队供外部消费者使用的弹窗事件，
            - 可选地通知本地回调端口弹窗已显示（除非标题包含"免责声明"），
            - 为免责声明和单按钮对话框调度自动解决，
            - 然后显示原始对话框，而后台监视器等待外部解决。

            返回:
                底层 messagebox 调用返回的值；对于确认对话框（`ask*`/`askokcancel`/`askyesno`/`askretrycancel`）这将是 True 或 False，对于其他对话框则是被包装函数返回的内容。
            """
            is_controlled = any(keyword in title for keyword in CONTROLLED_POPUPS)
            if not is_controlled:
                return original_func(title, message, **kwargs)
            import time
            popup_id = str(time.time())
            with _popup_wait_lock:
                _popup_wait_dict[popup_id] = {
                    'result': None,
                    'title': title,
                    'message': message,
                    'type': func_name,
                    'status': 'waiting'
                }
            popup_data = {
                'type': 'popup',
                'id': popup_id,
                'popup_type': func_name,
                'title': fix_encoding(title),
                'message': fix_encoding(message)
            }
            popup_event_queue.put(popup_data)
            
            # 非免责声明的弹窗立即推送通知（免责声明会自动关闭，走popup_closed流程）
            if CALLBACK_PORT and '免责声明' not in title:
                def send_popup_notification():
                    """
                    通知本地回调服务器已显示受控弹窗。

                    尝试短延迟 TCP 连接到 127.0.0.1:CALLBACK_PORT 并发送描述弹窗的 4 字节大端长度前缀的 UTF-8 JSON 消息。
                    JSON 包含：`event`（设为 "popup_show"）、`popup_id`、`popup_title`、`popup_message` 和 `popup_type`。
                    任何异常都被捕获并记录；函数不抛出异常。
                    """
                    import socket
                    import json
                    import time
                    time.sleep(0.3)  # 短暂延迟确保弹窗已创建
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(5)
                        s.connect(('127.0.0.1', CALLBACK_PORT))
                        msg = {
                            'event': 'popup_show',
                            'popup_id': popup_id,
                            'popup_title': fix_encoding(title),
                            'popup_message': fix_encoding(message),
                            'popup_type': func_name
                        }
                        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
                        s.sendall(len(data).to_bytes(4, 'big') + data)
                        s.close()
                        _log('info', f'[Callback] Popup "{title}" shown, notified port {CALLBACK_PORT}')
                    except Exception as e:
                        _log('warning', f'[Callback] Failed to notify popup: {e}')
                threading.Thread(target=send_popup_notification, daemon=True).start()
            
            # 免责声明自动关闭
            if '免责声明' in title:
                def auto_disclaimer():
                    """
                    短暂延迟后自动确认免责声明弹窗。

                    等待两秒，然后将由闭包范围的 `popup_id` 标识的弹窗标记为已解决，操作动作为 'ok'。
                    """
                    time.sleep(2)
                    _resolve_popup(popup_id, 'ok')
                threading.Thread(target=auto_disclaimer, daemon=True).start()
            
            # showwarning/showerror/showinfo 单按钮弹窗延迟1秒自动关闭
            if func_name in ['showinfo', 'showwarning', 'showerror']:
                def auto_close_single_button():
                    """
                    等待一秒，然后使用 'ok' 操作解决关联的弹窗。

                    此函数用于通过在 1 秒延迟后将其弹窗条目标记为 'ok' 结果来自动关闭单按钮对话框。
                    """
                    time.sleep(1)
                    _resolve_popup(popup_id, 'ok')
                threading.Thread(target=auto_close_single_button, daemon=True).start()
            
            return _create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs)
        return wrapper

    def _create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs):
        """
        显示可外部解决的受控 tkinter messagebox，可选地通知回调监听器。

        通过调用 `original_func(title, message, **kwargs)` 显示对话框，同时监控模块弹窗协调状态以获取 `popup_id`，
        解决后关闭对话框窗口（如果存在），从内部队列中移除弹窗，并可选地向配置的 CALLBACK_PORT 发送帧式 JSON 回调。
        调用将在返回前短暂等待监视器动作。

        参数:
            func_name (str): messagebox 变体的标识符（例如 'askyesno'、'askokcancel'、'showinfo'）。
            title (str): 用于定位和关闭对话框的窗口标题。
            message (str): 传递给对话框的消息文本。
            popup_id (str): 模块弹窗跟踪字典/队列中使用的唯一 ID。
            original_func (callable): 要调用的原始 tkinter.messagebox 函数（签名：title, message, **kwargs）。
            **kwargs: 转发到 `original_func` 的附加关键字参数。

        返回:
            bool 或 None: 对于 'askyesno'、'askokcancel' 和 'askretrycancel'，如果弹窗被肯定解决则返回 `True`，否定解决返回 `False`，未解决则返回 `None`；对于其他对话框类型返回 `None`。
        """
        import ctypes
        import time
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        popup_data = {'value': None, 'resolved': False}

        def is_window_exists(window_title):
            """
            检查具有确切 Unicode 标题的顶级窗口是否存在。

            参数:
                window_title (str): 要匹配的精确窗口标题（Unicode 字符串）。

            返回:
                bool: 如果具有给定标题的顶级窗口存在则返回 `True`，否则返回 `False`。
            """
            hwnd = user32.FindWindowW(None, window_title)
            return hwnd != 0

        def monitor():
            """
            等待受控弹窗被解决，如果存在则关闭其窗口，从弹窗队列中移除，并可选地通知本地回调端口。

            轮询模块的弹窗等待字典以获取此弹窗的解决状态；解决后，向弹窗窗口（如果找到）发送 WM_CLOSE，等待窗口消失，
            从共享队列中移除弹窗，并且——如果设置了 CALLBACK_PORT——触发回调通知器，发送描述关闭的长度前缀 JSON 事件。
            """
            while not popup_data['resolved']:
                with _popup_wait_lock:
                    info = _popup_wait_dict.get(popup_id)
                    if info and info.get('status') == 'resolved':
                        popup_data['value'] = info.get('result')
                        popup_data['resolved'] = True
                        if is_window_exists(title):
                            hwnd = user32.FindWindowW(None, title)
                            if hwnd:
                                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                        break
                time.sleep(0.1)

            time.sleep(2)

            for _ in range(30):
                if not is_window_exists(title):
                    break
                time.sleep(0.1)

            _remove_popup_from_queue(popup_id)

            if CALLBACK_PORT:
                def send_callback():
                    """
                    通知外部回调服务器弹窗已关闭。

                    构建包含 `event`、`popup_id`、`popup_title`、`popup_result` 和 `window_closed` 的 JSON 事件；
                    如果标题包含"免责声明"则事件为 `disclaimer_closed` 并包含 `bbc_ready: True`。
                    通过 TCP 连接到 127.0.0.1:CALLBACK_PORT，并使用模块的 4 字节大端长度前缀 JSON 帧格式发送事件。
                    记录成功或任何失败；不抛出异常。
                    """
                    import socket
                    import json
                    time.sleep(0.5)
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(5)
                        s.connect(('127.0.0.1', CALLBACK_PORT))
                        msg = {
                            'event': 'popup_closed',
                            'popup_id': popup_id,
                            'popup_title': title,
                            'popup_result': popup_data.get('value'),
                            'window_closed': not is_window_exists(title)
                        }
                        if "免责声明" in title:
                            msg['event'] = 'disclaimer_closed'
                            msg['bbc_ready'] = True
                        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
                        s.sendall(len(data).to_bytes(4, 'big') + data)
                        s.close()
                        _log('info', f'[Callback] Popup "{title}" closed, notified port {CALLBACK_PORT}')
                    except Exception as e:
                        _log('warning', f'[Callback] Failed to notify: {e}')
                threading.Thread(target=send_callback, daemon=True).start()

        t = threading.Thread(target=monitor, daemon=True)
        t.start()
        original_func(title, message, **kwargs)
        t.join(timeout=5)  
        with _popup_wait_lock:
            _popup_wait_dict.pop(popup_id, None)
        _remove_popup_from_queue(popup_id)
        result = popup_data['value'] if popup_data['resolved'] else None
        if func_name == 'askyesno':
            return bool(result)  # BBC askyesno 返回 True/False 布尔值
        elif func_name == 'askokcancel':
            return bool(result)  # BBC askokcancel 返回 True/False 布尔值
        elif func_name == 'askretrycancel':
            return bool(result)
        return None

    messagebox.showinfo = create_popup_wrapper('showinfo', original_messagebox['showinfo'])
    messagebox.showwarning = create_popup_wrapper('showwarning', original_messagebox['showwarning'])
    messagebox.showerror = create_popup_wrapper('showerror', original_messagebox['showerror'])
    messagebox.askokcancel = create_popup_wrapper('askokcancel', original_messagebox['askokcancel'])
    messagebox.askyesno = create_popup_wrapper('askyesno', original_messagebox['askyesno'])
    messagebox.askretrycancel = create_popup_wrapper('askretrycancel', original_messagebox['askretrycancel'])

    _tcp_server_instance = BBCServer(port)
    threading.Thread(target=_tcp_server_instance.start, daemon=True).start()
    _log('info', '[Server] TCP Server thread started')

    if CALLBACK_PORT:
        def send_callback():
            """
            Notify a local callback listener that the TCP server has started.
            
            Sleeps 0.5 seconds, then connects to 127.0.0.1:CALLBACK_PORT and sends a 4-byte big-endian length-prefixed UTF-8 JSON payload:
            {'event': 'server_started', 'server_port': port, 'bbc_ready': _bb_window_global is not None}.
            Logs an info message on success and a warning on failure. No value is returned.
            """
            import socket
            import json
            import time
            time.sleep(0.5)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect(('127.0.0.1', CALLBACK_PORT))
                msg = {
                    'event': 'server_started',
                    'server_port': port,
                    'bbc_ready': _bb_window_global is not None
                }
                data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
                s.sendall(len(data).to_bytes(4, 'big') + data)
                s.close()
                _log('info', f'[Callback] Notified port {CALLBACK_PORT}')
            except Exception as e:
                _log('warning', f'[Callback] Failed to notify port {CALLBACK_PORT}: {e}')
        threading.Thread(target=send_callback, daemon=True).start()