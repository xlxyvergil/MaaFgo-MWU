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

TCP_PORT = 25001
CALLBACK_PORT = 25002

# ==================== BBC 窗口注册 ====================

def update_bb_window(bb_window):
    global _bb_window_global
    _bb_window_global = bb_window
    _log('info', '[Server] BBC window registered')

def get_bb_page():
    if _bb_window_global is None:
        return None
    return _bb_window_global.pages[0]

# ==================== 弹窗机制 ====================

def _remove_popup_from_queue(popup_id):
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
            _bb_window_global.pagebar.tags[page.idx].createText(True)
            _bb_window_global.updateConnectLst(page.idx)
            _log('info', f'[Connection] MuMu connected: {emulator_name}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] MuMu connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def connect_ld(path=None, index=0):
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
            _bb_window_global.pagebar.tags[page.idx].createText(True)
            _bb_window_global.updateConnectLst(page.idx)
            _log('info', f'[Connection] LD connected: index={index}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] LD connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def connect_adb(ip):
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
            device = Android(ip, page.server, USE_AS_BOTH, cap_method="Minicap")
            if not device.available:
                device.disconnect()
                return {'success': False, 'error': 'ADB device unavailable'}
            page.snapshotDevice = page.operateDevice = page.device.snapshotDevice = page.device.operateDevice = device
            _bb_window_global.pagebar.tags[page.idx].createText(True)
            _bb_window_global.updateConnectLst(page.idx)
            _log('info', f'[Connection] ADB connected: {ip}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Connection] ADB connect failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def disconnect():
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
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not filename:
            return {'success': False, 'error': 'filename required'}

        # BBC 工作目录是 dist/BBchannel64，settings 在 ../settings
        # __file__ = d:\BBC\BBchannel\dist\BBchannel64\bbc_tcp_server.py
        # 3层 dirname = d:\BBC\BBchannel
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        config_path = os.path.join(base_dir, "settings", filename)
        if not os.path.exists(config_path):
            return {'success': False, 'error': f'Config file not found: {config_path}'}
        try:
            with open(config_path, "r", encoding="utf8") as fp:
                SS = json.load(fp)
            SS["connectMode"] = page.SS.get("connectMode", None)
            SS["snapshotDevice"] = page.SS.get("snapshotDevice", None)
            SS["operateDevice"] = page.SS.get("operateDevice", None)
            page.SS = SS
            page.reset()
            _bb_window_global.saveJsons()
            _log('info', f'[Config] Loaded: {filename}')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Config] Load failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def save_config(filename):
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        import os, json
        if not filename:
            return {'success': False, 'error': 'filename required'}
        try:
            config_path = os.path.join("settings", filename)
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, "w", encoding="utf8") as fp:
                json.dump(page.SS, fp, ensure_ascii=False, indent=4)
            _log('info', f'[Config] Saved: {filename}')
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    @staticmethod
    def get_config():
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
        "自动编队爬塔": 1
    }

    @staticmethod
    def set_apple_type(apple_type):
        ensure_imports()
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        if apple_type not in BattleSettingsAPI.APPLE_MAP:
            return {'success': False, 'error': f'Unknown apple type: {apple_type}'}
        try:
            page.appleSet.appleType = CT.Gold if apple_type == "gold" else getattr(CT, apple_type.capitalize(), CT.Gold)
            page.appleSet.appleIconPhoto = page.appleSet.getAppleIconPhoto()
            if hasattr(page.appleSet, 'appleIcon'):
                page.appleSet.appleIcon.config(image=page.appleSet.appleIconPhoto)
            _log('info', f'[Battle] Apple type set: {apple_type}')
            return {'success': True, 'apple_type': apple_type}
        except Exception as e:
            _log('warning', f'[Battle] Apple type set but UI update failed: {e}')
            return {'success': True, 'apple_type': apple_type, 'warning': str(e)}

    @staticmethod
    def set_run_times(times):
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
            btn_x = page.start.winfo_width() // 2
            btn_y = page.start.winfo_height() // 2
            page.start.event_generate("<Button-1>", x=btn_x, y=btn_y)
            _log('info', '[Battle] Battle started')
            return {'success': True}
        except Exception as e:
            _log('error', f'[Battle] Start failed: {e}')
            return {'success': False, 'error': str(e)}

    @staticmethod
    def stop_battle():
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
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        return {'success': False, 'error': 'Pause not implemented'}

    @staticmethod
    def resume_battle():
        page = get_bb_page()
        if page is None:
            return {'success': False, 'error': 'BBC window not ready'}
        return {'success': False, 'error': 'Resume not implemented'}


class StatusAPI:
    @staticmethod
    def get_status():
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
        import time
        start = time.time()
        while time.time() - start < timeout:
            if popup_event_queue and not popup_event_queue.empty():
                return {'success': True, 'has_popup': True}
            time.sleep(0.5)
        return {'success': True, 'has_popup': False}

    @staticmethod
    def get_ui_status():
        """获取UI状态信息（包括topLabel提示文本）"""
        page = get_bb_page()
        if page is None:
            return {
                'success': True,
                'top_label': '',
                'device_running': False,
                'battle_running': False
            }
        try:
            # 获取topLabel文本
            top_label_text = ''
            if hasattr(page, 'topLabel'):
                try:
                    top_label_text = page.topLabel.cget('text')
                except:
                    pass
            
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
        self.client = client_socket
        self.addr = addr
        self.server = server
        self.running = True

    def handle(self):
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
        data = b''
        while len(data) < n:
            chunk = self.client.recv(n - len(data))
            if not chunk:
                return b''
            data += chunk
        return data

    def stop(self):
        self.running = False


class BBCServer:
    def __init__(self, port=25001):
        self.port = port
        self.socket = None
        self.running = False
        self.clients = []
        self.clients_lock = __import__('threading').Lock()

    def add_client(self, client):
        with self.clients_lock:
            self.clients.append(client)

    def remove_client(self, client):
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)

    def start(self):
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
        self.running = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass


# ==================== 启动入口 ====================

_tcp_server_instance = None

def start_tcp_server(bb_window, port=25001):
    import threading
    import queue
    from tkinter import messagebox

    global _bb_window_global
    global popup_event_queue
    global _popup_wait_lock
    global _popup_wait_dict
    global _tcp_server_instance

    _bb_window_global = bb_window
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
        if isinstance(s, bytes):
            return s.decode('utf-8', errors='replace')
        try:
            return s.encode('latin-1').decode('gbk', errors='replace')
        except:
            return s

    def create_popup_wrapper(func_name, original_func):
        def wrapper(title, message, **kwargs):
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
            
            if '免责声明' in title:
                def auto_disclaimer():
                    time.sleep(2)
                    _resolve_popup(popup_id, 'ok')
                threading.Thread(target=auto_disclaimer, daemon=True).start()
            return _create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs)
        return wrapper

    def _create_controlled_dialog(func_name, title, message, popup_id, original_func, **kwargs):
        import ctypes
        import time
        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        popup_data = {'value': None, 'resolved': False}

        def is_window_exists(window_title):
            hwnd = user32.FindWindowW(None, window_title)
            return hwnd != 0

        def monitor():
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
        t.join(timeout=1)
        with _popup_wait_lock:
            _popup_wait_dict.pop(popup_id, None)
        _remove_popup_from_queue(popup_id)
        result = popup_data['value'] if popup_data['resolved'] else None
        if func_name == 'askyesno':
            return result == 'yes'
        elif func_name == 'askokcancel':
            return result == 'ok'
        elif func_name == 'askretrycancel':
            return result == 'retry'
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
