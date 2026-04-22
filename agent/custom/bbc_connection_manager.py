"""
BBC 连接管理器 - 单例模式，管理 BBC TCP 连接、回调监听、进程启动和模拟器连接
"""
import json
import os
import socket
import struct
import subprocess
import threading
import time
import logging
import psutil
from typing import Optional
import mfaalog

logger = logging.getLogger("BbcConnectionManager")

# BBC TCP 配置
BBC_TCP_HOST = "127.0.0.1"
BBC_TCP_PORT = 25001
BBC_CALLBACK_PORT = 25002

# BBC 路径配置
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BBC_PATH = os.path.join(AGENT_ROOT, '..', 'BBchannel')
BBC_EXE_PATH = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
BBC_EXE_PATH = os.path.abspath(BBC_EXE_PATH)


class BbcConnectionManager:
    """BBC 连接管理器 - 每次创建新实例"""
    
    def __init__(self):
        # 先尝试关闭端口上的旧监听（如果有）
        """
        Initialize a BbcConnectionManager instance.
        
        Sets up internal sockets, threading primitives, state tracking, and message queue; cleans any existing process bound to the callback port and starts the permanent callback listener. After initialization the instance has:
        - _tcp_sock: TCP socket or None
        - _callback_server: callback listening socket or None
        - _callback_thread: background thread for callback handling or None
        - _message_queue: list used as the incoming message queue (protected by _queue_lock)
        - _queue_lock: Lock protecting the message queue
        - _popup_callback: optional callable invoked for popup events
        - _bbc_ready_event: Event used to signal BBC readiness
        - _state: dict containing 'connected', 'callback_listening', and 'bbc_process'
        - _state_lock: Lock protecting _state
        
        Side effects:
        - Attempts to free the configured callback port if occupied.
        - Starts the permanent callback listener thread.
        """
        self._cleanup_port()
        
        self._tcp_sock: Optional[socket.socket] = None
        self._callback_server: Optional[socket.socket] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._message_queue = []  # 消息队列
        self._queue_lock = threading.Lock()
        self._popup_callback = None  # 弹窗回调函数
        self._bbc_ready_event = threading.Event()  # BBC就绪事件
        self._state = {
            'connected': False,
            'callback_listening': False,
            'bbc_process': None,  # BBC 进程对象
        }
        self._state_lock = threading.Lock()
        
        mfaalog.info(f"[BbcConnectionManager] 创建新实例, ID: {id(self)}, Event ID: {id(self._bbc_ready_event)}")
        
        # 自动启动回调监听
        self._start_permanent_listener()
    
    def _reset_state(self):
        """
        Reset the manager's runtime state and close or terminate active resources.
        
        Stops the callback listener, closes the callback server socket, joins the callback listener thread if running, disconnects any TCP connection, terminates the BBC process if present, and waits briefly to allow resources to clean up.
        """
        # 停止回调监听
        with self._state_lock:
            self._state['callback_listening'] = False
        
        if self._callback_server:
            try:
                self._callback_server.close()
            except:
                pass
        
        if self._callback_thread and self._callback_thread.is_alive():
            self._callback_thread.join(timeout=3)
        
        # 断开TCP连接
        self.disconnect_tcp()
        
        # 终止BBC进程
        if self._state.get('bbc_process'):
            try:
                self._kill_bbc_process()
            except:
                pass
        
        time.sleep(0.5)
        mfaalog.info("[BbcConnectionManager] 旧状态已清理")
    
    def _cleanup_port(self):
        """
        Terminate any process listening on the configured BBC callback TCP port.
        
        Searches system listening sockets for BBC_CALLBACK_PORT and, if a listening process is found, attempts to forcibly terminate that process and logs the outcome. Logs a message if the port is free and emits a warning if an error occurs while checking or terminating processes.
        """
        try:
            import subprocess
            # Windows: 查找占用端口的 PID
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True,
                timeout=5,
                encoding='gbk'  # Windows netstat 输出是 GBK 编码
            )
            
            if not result.stdout:
                mfaalog.debug(f"[BbcConnectionManager] netstat 返回空")
                return
            
            for line in result.stdout.splitlines():
                if f':{BBC_CALLBACK_PORT}' in line and 'LISTENING' in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        mfaalog.warning(f"[BbcConnectionManager] 检测到端口 {BBC_CALLBACK_PORT} 被 PID {pid} 占用，终止进程...")
                        try:
                            subprocess.run(['taskkill', '/F', '/PID', pid], 
                                         capture_output=True, timeout=3)
                            mfaalog.info(f"[BbcConnectionManager] 已终止 PID {pid}")
                            time.sleep(0.5)
                        except Exception as e:
                            mfaalog.error(f"[BbcConnectionManager] 终止进程失败: {e}")
                    break
            else:
                mfaalog.info(f"[BbcConnectionManager] 端口 {BBC_CALLBACK_PORT} 空闲")
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 端口检查异常: {e}")
    
    def _start_permanent_listener(self):
        """
        Start a background TCP listener that accepts permanent callback connections.
        
        Binds a server socket to 127.0.0.1:BBC_CALLBACK_PORT, sets self._callback_server and
        self._state['callback_listening'], and starts a daemon thread running
        self._permanent_callback_loop. Errors during startup are logged.
        """
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', BBC_CALLBACK_PORT))
            server_sock.listen(5)
            server_sock.settimeout(2)
            
            with self._state_lock:
                self._callback_server = server_sock
                self._state['callback_listening'] = True
            
            self._callback_thread = threading.Thread(
                target=self._permanent_callback_loop,
                args=(server_sock,),
                daemon=True
            )
            self._callback_thread.start()
            
            mfaalog.info(f"[BbcConnectionManager] 永久回调监听已启动 on port {BBC_CALLBACK_PORT}")
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 启动永久监听失败: {e}")
    
    def _restart_callback_listener(self):
        """
        Restart the callback listener thread and rebind the readiness event to this instance.
        
        Stops the existing listener (marks listening state false, closes the server socket and joins the thread), replaces the instance's readiness Event used for BBC-ready signaling, sleeps briefly to allow resources to settle, and starts a new permanent callback listener.
        """
        # 停止旧线程
        with self._state_lock:
            self._state['callback_listening'] = False
        
        if self._callback_server:
            try:
                self._callback_server.close()
            except:
                pass
        
        if self._callback_thread and self._callback_thread.is_alive():
            self._callback_thread.join(timeout=3)
        
        # 创建新的 Event 对象，确保新线程使用全新的同步对象
        self._bbc_ready_event = threading.Event()
        mfaalog.info(f"[BbcConnectionManager] 创建新的Event对象, ID: {id(self._bbc_ready_event)}")
        
        time.sleep(0.5)
        
        # 启动新线程
        self._start_permanent_listener()
    
    def _permanent_callback_loop(self, server_sock: socket.socket):
        """
        Run the permanent callback listener loop and enqueue received callback messages.
        
        This loop accepts inbound connections on the provided listening socket, reads a 4-byte big-endian length prefix followed by a JSON payload, decodes the payload to a dict and appends it to the instance message queue. When a message's `event` is one of `server_started` or `disclaimer_closed`, the instance readiness event (`self._bbc_ready_event`) is set. If an `event` equals `popup_show`, the configured popup callback (if any) is invoked with the message. A message with `event == '__shutdown__'` causes the loop to exit. Socket timeouts are ignored; other exceptions are logged and the loop continues unless callback listening has been disabled.
        
        Parameters:
            server_sock (socket.socket): A listening TCP socket bound to the BBC callback port; the function calls `accept()` on this socket.
        
        """
        mfaalog.info("[BbcConnectionManager] 永久回调监听循环开始")
        
        while True:
            with self._state_lock:
                if not self._state['callback_listening']:
                    break
            
            try:
                client_sock, addr = server_sock.accept()
                client_sock.settimeout(5)
                
                # 接收消息
                length_bytes = self._recv_all(client_sock, 4)
                if not length_bytes:
                    client_sock.close()
                    continue
                
                length = struct.unpack('>I', length_bytes)[0]
                data = self._recv_all(client_sock, length)
                if not data:
                    client_sock.close()
                    continue
                
                msg = json.loads(data.decode('utf-8'))
                
                # 处理关闭信号
                if msg.get('event') == '__shutdown__':
                    mfaalog.info(f"[BbcConnectionManager] 收到关闭信号，退出回调线程")
                    client_sock.close()
                    break
                
                # 根据事件类型输出日志
                event = msg.get('event')
                if event == 'popup_show':
                    mfaalog.info(f"[BbcConnectionManager] 收到弹窗: {msg.get('popup_title', '')}")
                elif event == 'popup_closed':
                    mfaalog.debug(f"[BbcConnectionManager] 弹窗已关闭: {msg.get('popup_title', '')}")
                else:
                    mfaalog.debug(f"[BbcConnectionManager] 收到回调: {msg}")
                
                # 触发BBC就绪事件
                if event in ['server_started', 'disclaimer_closed']:
                    mfaalog.info(f"[BbcConnectionManager] BBC就绪信号: {event}, Event对象ID: {id(self._bbc_ready_event)}, Event状态: {self._bbc_ready_event.is_set()}")
                    self._bbc_ready_event.set()
                    mfaalog.info(f"[BbcConnectionManager] 已触发事件, Event状态: {self._bbc_ready_event.is_set()}")
                
                # 放入消息队列
                with self._queue_lock:
                    self._message_queue.append(msg)
                
                # 触发弹窗回调（如果是弹窗事件）
                if msg.get('event') == 'popup_show':
                    mfaalog.info(f"[BbcConnectionManager] 准备触发回调, callback_exists={self._popup_callback is not None}")
                    if self._popup_callback:
                        try:
                            mfaalog.info("[BbcConnectionManager] 开始执行弹窗回调")
                            self._popup_callback(msg)
                            mfaalog.info("[BbcConnectionManager] 弹窗回调执行完成")
                        except Exception as e:
                            import traceback
                            mfaalog.error(f"[BbcConnectionManager] 弹窗回调执行失败: {e}")
                            mfaalog.error(traceback.format_exc())
                    else:
                        mfaalog.warning("[BbcConnectionManager] 弹窗回调未设置")
                
                client_sock.close()
            except socket.timeout:
                continue
            except Exception as e:
                with self._state_lock:
                    if not self._state['callback_listening']:
                        break
                mfaalog.warning(f"[BbcConnectionManager] 回调接收异常: {e}")
                continue
        
        mfaalog.info("[BbcConnectionManager] 永久回调监听循环结束")
    
    def get_message(self, timeout: float = 1.0) -> Optional[dict]:
        """从消息队列获取一条消息（阻塞等待）"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._queue_lock:
                if self._message_queue:
                    return self._message_queue.pop(0)
            time.sleep(0.1)  # 缩短为0.1秒，提高响应速度
        return None
    
    def get_messages_by_title(self, title_keyword: str, timeout: float = 2.0) -> list:
        """获取包含指定关键词的消息列表"""
        messages = []
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self._queue_lock:
                for msg in self._message_queue[:]:
                    popup_title = msg.get('popup_title', '')
                    if title_keyword in popup_title:
                        messages.append(msg)
                        self._message_queue.remove(msg)
            
            if messages:
                break
            time.sleep(0.1)  # 缩短为0.1秒，提高响应速度
        
        return messages
    
    def set_popup_callback(self, callback):
        """设置弹窗回调函数"""
        self._popup_callback = callback
        mfaalog.info("[BbcConnectionManager] 弹窗回调已设置")
    
    def connect_tcp(self, timeout: int = 10) -> bool:
        """建立 TCP 连接"""
        with self._state_lock:
            if self._state['connected'] and self._tcp_sock:
                # 测试连接是否仍然有效
                try:
                    self._tcp_sock.settimeout(1)
                    self._tcp_sock.send(b'\x00\x00\x00\x00')  # 空消息测试
                    return True
                except:
                    self._disconnect_tcp()
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((BBC_TCP_HOST, BBC_TCP_PORT))
            
            with self._state_lock:
                self._tcp_sock = sock
                self._state['connected'] = True
            
            mfaalog.info(f"[BbcConnectionManager] TCP 连接成功 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] TCP 连接失败: {e}")
            return False
    
    def disconnect_tcp(self):
        """断开 TCP 连接"""
        with self._state_lock:
            self._disconnect_tcp()
    
    def _disconnect_tcp(self):
        """内部断开方法（需持有锁）"""
        if self._tcp_sock:
            try:
                self._tcp_sock.close()
            except:
                pass
            self._tcp_sock = None
            self._state['connected'] = False
    
    def send_command(self, cmd: str, args: dict = None, timeout: int = 10) -> dict:
        """发送命令并等待响应"""
        with self._state_lock:
            if not self._tcp_sock or not self._state['connected']:
                return {'success': False, 'error': 'Not connected'}
            sock = self._tcp_sock
        
        data = {'cmd': cmd, 'args': args or {}}
        try:
            msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
            msg_with_len = len(msg).to_bytes(4, 'big') + msg
            sock.sendall(msg_with_len)
            
            # 接收响应
            original_timeout = sock.gettimeout()
            sock.settimeout(timeout)
            
            length_bytes = self._recv_all(sock, 4)
            if not length_bytes:
                return {'success': False, 'error': 'Connection closed'}
            
            length = struct.unpack('>I', length_bytes)[0]
            response_data = self._recv_all(sock, length)
            if not response_data:
                return {'success': False, 'error': 'No response data'}
            
            sock.settimeout(original_timeout)
            return json.loads(response_data.decode('utf-8'))
        except socket.timeout:
            return {'success': False, 'error': f'Timeout (cmd={cmd})'}
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 发送命令失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _recv_all(self, sock: socket.socket, n: int) -> bytes:
        """接收指定字节数"""
        data = b''
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except:
                return None
        return data
    
    def is_connected(self) -> bool:
        """检查TCP连接是否有效"""
        with self._state_lock:
            if not self._tcp_sock or not self._state['connected']:
                return False
            
            # 测试连接是否仍然可用
            try:
                original_timeout = self._tcp_sock.gettimeout()
                self._tcp_sock.settimeout(1)
                test_msg = json.dumps({'cmd': 'get_status', 'args': {}}).encode('utf-8')
                msg_with_len = len(test_msg).to_bytes(4, 'big') + test_msg
                self._tcp_sock.sendall(msg_with_len)
                
                length_bytes = self._recv_all(self._tcp_sock, 4)
                if not length_bytes:
                    return False
                
                length = struct.unpack('>I', length_bytes)[0]
                response_data = self._recv_all(self._tcp_sock, length)
                if not response_data:
                    return False
                
                self._tcp_sock.settimeout(original_timeout)
                return True
            except:
                self._disconnect_tcp()
                return False
    
    def ensure_connected(self, timeout: int = 5) -> bool:
        """确保连接有效，无效则重连"""
        if self.is_connected():
            mfaalog.debug("[BbcConnectionManager] 连接有效")
            return True
        
        mfaalog.info("[BbcConnectionManager] 连接失效，尝试重连...")
        return self.connect_tcp(timeout=timeout)
    
    def clear_message_queue(self):
        """清空消息队列"""
        with self._queue_lock:
            self._message_queue.clear()
        mfaalog.debug("[BbcConnectionManager] 消息队列已清空")
    
    # ==================== BBC 进程管理 ====================
    
    def _find_bbc_process(self):
        """查找BBC进程"""
        try:
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    cmdline = proc.info.get('cmdline', [])
                    if cmdline and any('BBchannel.exe' in arg for arg in cmdline):
                        return proc
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return None
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 查找进程失败: {e}")
            return None
    
    def _kill_bbc_process(self, proc=None):
        """终止BBC进程"""
        if proc is None:
            with self._state_lock:
                proc = self._state.get('bbc_process')
        
        try:
            # 检查进程是否还在运行 (subprocess.Popen 用 poll())
            if proc and proc.poll() is None:
                mfaalog.info(f"[BbcConnectionManager] 终止BBC进程 PID: {proc.pid}")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
                mfaalog.info("[BbcConnectionManager] BBC进程已终止")
                with self._state_lock:
                    self._state['bbc_process'] = None
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 终止进程失败: {e}")
    
    def _launch_bbc(self):
        """
        Start the BBC executable as a subprocess and record its process object.
        
        The method attempts to launch the executable at BBC_EXE_PATH using the executable's directory as the working directory.
        Standard output and standard error are redirected to files named "bbc_stdout.log" and "bbc_stderr.log" created in the executable's directory.
        If the executable filename ends with "_debug.exe", the subprocess is started with a new console.
        On successful launch, the subprocess.Popen object is stored in self._state['bbc_process'].
        
        Returns:
            subprocess.Popen or None: The Popen object for the launched BBC process if successful, `None` if the executable does not exist or launch fails.
        """
        if not os.path.exists(BBC_EXE_PATH):
            mfaalog.error(f"[BbcConnectionManager] BBC可执行文件不存在: {BBC_EXE_PATH}")
            return None
        
        bbc_dir = os.path.dirname(BBC_EXE_PATH)
        _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
        _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
        
        mfaalog.info(f"[BbcConnectionManager] 启动BBC: {BBC_EXE_PATH}")
        mfaalog.info(f"[BbcConnectionManager] 调试模式: {_is_debug}, 工作目录: {bbc_dir}")
        
        try:
            # 重定向输出到文件
            stdout_file = open(os.path.join(bbc_dir, 'bbc_stdout.log'), 'w', encoding='utf-8')
            stderr_file = open(os.path.join(bbc_dir, 'bbc_stderr.log'), 'w', encoding='utf-8')
            
            proc = subprocess.Popen(
                [BBC_EXE_PATH],
                cwd=bbc_dir,
                creationflags=_creation_flags,
                stdout=stdout_file,
                stderr=stderr_file
            )
            mfaalog.info(f"[BbcConnectionManager] BBC进程已启动，PID: {proc.pid}")
            
            with self._state_lock:
                self._state['bbc_process'] = proc
            
            return proc
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 启动BBC失败: {e}")
            return None
    
    def _wait_for_bbc_ready(self, timeout: int = 30) -> bool:
        """
        Waits for the BBC readiness event to be signaled.
        
        Parameters:
            timeout (int): Maximum number of seconds to wait for the readiness event.
        
        Returns:
            bool: `True` if the readiness event was signaled within `timeout` seconds, `False` otherwise.
        """
        mfaalog.info(f"[BbcConnectionManager] 等待BBC就绪 (超时{timeout}s)...")
        ready = self._bbc_ready_event.wait(timeout=timeout)
        
        if ready:
            mfaalog.info("[BbcConnectionManager] BBC 就绪事件已触发")
            return True
        else:
            mfaalog.warning(f"[BbcConnectionManager] 等待 BBC 就绪超时 ({timeout}s)")
            return False
    
    # ==================== 模拟器连接 ====================
    
    def connect_emulator(self, connect_cmd: str, connect_args: dict, timeout: int = 30) -> bool:
        """连接模拟器（封装 BBC 命令）"""
        try:
            # auto模式不发送连接命令，直接等待
            if connect_args.get('mode') == 'auto':
                mfaalog.info("[BbcConnectionManager] Auto模式，等待BBC自动连接...")
                time.sleep(5)
                return True
            
            # 先等待 BBC UI 完全就绪
            mfaalog.info("[BbcConnectionManager] 等待 BBC UI 完全就绪...")
            time.sleep(5)
            
            # 发送连接命令
            mfaalog.info(f"[BbcConnectionManager] 执行连接命令: {connect_cmd}, 参数: {connect_args}")
            result = self.send_command(connect_cmd, connect_args, timeout=timeout)
            
            if not result.get('success'):
                error_msg = result.get('error', '未知错误')
                mfaalog.error(f"[BbcConnectionManager] 连接失败: {error_msg}")
                return False
            
            mfaalog.info("[BbcConnectionManager] 连接命令执行成功")
            time.sleep(5)
            
            # 验证连接状态
            status_result = self.send_command('get_connection', {}, timeout=5)
            device_available = status_result.get('available', False)
            device_connected = status_result.get('connected', False)
            
            if device_available or device_connected:
                mfaalog.info(f"[BbcConnectionManager] 模拟器连接成功 (available={device_available}, connected={device_connected})")
                return True
            else:
                mfaalog.warning(f"[BbcConnectionManager] 模拟器未连接 (available={device_available}, connected={device_connected})")
                return False
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 连接异常: {e}")
            return False
    
    # ==================== 完整重启流程 ====================
    
    def restart_bbc_and_connect(self, connect_cmd: str, connect_args: dict, max_retries: int = 5) -> bool:
        """
        Restart the BBC application and attempt to connect the emulator, retrying the full restart-and-connect sequence up to `max_retries` times.
        
        This method clears the message queue and readiness event for each attempt, terminates any existing BBC process, launches a new BBC process, waits for BBC readiness, establishes the TCP connection, and then attempts to connect the emulator. It may terminate the launched BBC process on failure and will perform multiple attempts until success or until `max_retries` is reached.
        
        Parameters:
            connect_cmd (str): Emulator connect command name (e.g., command used by BBC to initiate emulator connection).
            connect_args (dict): Arguments passed to the emulator connect command.
            max_retries (int): Maximum number of restart-and-connect attempts (default 5).
        
        Returns:
            bool: `true` if the BBC was restarted and the emulator connection succeeded within the allowed attempts, `false` otherwise.
        """
        mfaalog.info(f"[BbcConnectionManager] ========== 开始重启 BBC ==========")
        
        for attempt in range(1, max_retries + 1):
            mfaalog.info(f"[BbcConnectionManager] 第{attempt}次启动尝试")
            
            # 清空本次尝试的消息队列和就绪事件
            mfaalog.info(f"[BbcConnectionManager] 清空消息队列和就绪事件 (尝试 {attempt})")
            self.clear_message_queue()
            self._bbc_ready_event.clear()
            
            # 1. 杀掉旧进程
            mfaalog.info(f"[BbcConnectionManager] 终止旧BBC进程 (尝试 {attempt})")
            self._kill_bbc_process()
            time.sleep(5)
            
            # 2. 启动新进程
            bbc_proc = self._launch_bbc()
            if not bbc_proc:
                mfaalog.error(f"[BbcConnectionManager] BBC进程启动失败 (尝试 {attempt})")
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 3. 等待 BBC 就绪
            mfaalog.info("[BbcConnectionManager] 等待BBC就绪...")
            ready = self._wait_for_bbc_ready(timeout=30)
            if not ready:
                mfaalog.warning(f"[BbcConnectionManager] BBC就绪超时 (尝试 {attempt})")
                self._kill_bbc_process(bbc_proc)
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 4. 建立 TCP 连接
            mfaalog.info("[BbcConnectionManager] BBC已就绪，建立TCP连接...")
            if not self.connect_tcp(timeout=10):
                mfaalog.warning(f"[BbcConnectionManager] TCP连接失败 (尝试 {attempt})")
                self._kill_bbc_process(bbc_proc)
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 5. 连接模拟器
            mfaalog.info("[BbcConnectionManager] 连接模拟器...")
            if self.connect_emulator(connect_cmd, connect_args, timeout=30):
                mfaalog.info("[BbcConnectionManager] BBC重启并连接成功")
                return True
            else:
                mfaalog.warning(f"[BbcConnectionManager] 模拟器连接失败 (尝试 {attempt})")
                self._kill_bbc_process(bbc_proc)
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
        
        return False
    
    def get_state(self) -> dict:
        """获取连接状态"""
        with self._state_lock:
            return self._state.copy()
    
    def get_last_popup(self) -> Optional[dict]:
        """获取最近的弹窗信息"""
        with self._state_lock:
            return self._state.get('last_popup')
    
    def check_emulator_params_match(self, connect_cmd: str, expected_args: dict, actual_params: dict) -> bool:
        """检查模拟器参数是否匹配"""
        try:
            if connect_cmd == 'connect_mumu':
                # MuMu: 检查 path, index, pkg, app_index
                path_match = expected_args.get('path', '') == actual_params.get('mumu_path', '')
                index_match = expected_args.get('index', 0) == actual_params.get('emulator_index', 0)
                pkg_match = expected_args.get('pkg', '') == actual_params.get('pkg', '')
                app_index_match = expected_args.get('app_index', 0) == actual_params.get('app_index', 0)
                return path_match and index_match and pkg_match and app_index_match
            
            elif connect_cmd == 'connect_ld':
                # LD: 检查 path, index
                path_match = expected_args.get('path', '') == actual_params.get('ld_path', '')
                index_match = expected_args.get('index', 0) == actual_params.get('emulator_index', 0)
                return path_match and index_match
            
            elif connect_cmd == 'connect_adb':
                # ADB: 检查 IP
                expected_ip = expected_args.get('ip', '')
                actual_ip = actual_params.get('ip', '')
                return expected_ip == actual_ip
            
            elif connect_cmd == 'auto':
                # auto 模式，只要有参数就算匹配
                return bool(actual_params)
            
            return False
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 参数匹配检查失败: {e}")
            return False
    
    def cleanup(self):
        """清理所有资源（不关闭永久监听）"""
        self.disconnect_tcp()
        mfaalog.info("[BbcConnectionManager] TCP连接已清理")


# 全局实例
bbc_manager = BbcConnectionManager()
