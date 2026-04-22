"""
BBC 连接管理器 - 管理 BBC TCP 连接、回调监听、进程启动和模拟器连接
每次创建新实例，不使用单例模式
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
        try:
            self._cleanup_port()
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 初始化期间清理端口失败: {e}")
            raise RuntimeError(f"BbcConnectionManager 初始化失败: 端口清理阶段出错: {e}") from e

        self._tcp_sock: Optional[socket.socket] = None
        self._callback_server: Optional[socket.socket] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._message_queue = []
        self._queue_lock = threading.Lock()
        self._popup_callback = None
        self._bbc_ready_event = threading.Event()
        self._state = {
            'connected': False,
            'callback_listening': False,
            'bbc_process': None,
            'bbc_stdout_file': None,
            'bbc_stderr_file': None,
        }
        self._state_lock = threading.Lock()
        self._socket_lock = threading.Lock()

        mfaalog.info(f"[BbcConnectionManager] 创建新实例, ID: {id(self)}, Event ID: {id(self._bbc_ready_event)}")

        if not self._start_permanent_listener():
            mfaalog.error("[BbcConnectionManager] 启动永久监听失败")
            raise RuntimeError("BbcConnectionManager 初始化失败: 永久监听无法启动")
    
    def _cleanup_port(self):
        """
        终止占用配置回调 TCP 端口的任何进程。

        运行平台特定的检查（使用 Windows netstat 输出）查找绑定到 BBC_CALLBACK_PORT 的 LISTENING 状态 socket；
        如果找到，尝试使用 taskkill 终止所属进程并记录结果。
        如果未找到监听器则记录端口空闲。
        检查或终止期间的任何意外错误都会被捕获并记录为警告。
        """
        try:
            # Windows: 查找占用端口的 PID
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True,
                timeout=5,
                encoding='gbk'  # Windows netstat 输出是 GBK 编码
            )
            
            if not result.stdout:
                mfaalog.debug("[BbcConnectionManager] netstat 返回空")
                return
            
            for line in result.stdout.splitlines():
                if f':{BBC_CALLBACK_PORT}' in line and 'LISTENING' in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        mfaalog.warning(f"[BbcConnectionManager] 检测到端口 {BBC_CALLBACK_PORT} 被 PID {pid} 占用，验证进程...")
                        try:
                            # Verify the process is a BBC/agent process before killing
                            pid_int = int(pid)
                            proc = psutil.Process(pid_int)
                            cmdline = proc.cmdline()
                            # Check if cmdline contains BBC or agent identifiers
                            is_bbc_or_agent = any(
                                'BBchannel' in arg or 'bbc' in arg.lower() or 'agent' in arg.lower()
                                for arg in cmdline
                            )
                            if is_bbc_or_agent:
                                mfaalog.warning(f"[BbcConnectionManager] PID {pid} 是BBC/agent进程，终止...")
                                subprocess.run(['taskkill', '/F', '/PID', pid],
                                             capture_output=True, timeout=3)
                                mfaalog.info(f"[BbcConnectionManager] 已终止 PID {pid}")
                                time.sleep(0.5)
                            else:
                                mfaalog.error(f"[BbcConnectionManager] PID {pid} 不是BBC/agent进程 (cmdline={cmdline})，不终止")
                                raise RuntimeError(f"Port {BBC_CALLBACK_PORT} is occupied by non-BBC process PID {pid}")
                        except psutil.NoSuchProcess:
                            mfaalog.warning(f"[BbcConnectionManager] PID {pid} 已不存在")
                        except Exception as e:
                            mfaalog.error(f"[BbcConnectionManager] 验证或终止进程失败: {e}")
                            raise
                    break
            else:
                mfaalog.info(f"[BbcConnectionManager] 端口 {BBC_CALLBACK_PORT} 空闲")
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 端口检查异常: {e}")
    
    def _start_permanent_listener(self) -> bool:
        """
        启动绑定到 localhost:BBC_CALLBACK_PORT 的后台 TCP 监听器，用于接收 BBC 回调。

        将内部状态 'callback_listening' 设为 True，将服务器 socket 存储在 self._callback_server 中，
        并生成一个守护线程运行 _permanent_callback_loop 来接受和处理传入的回调连接。

        返回:
            bool: 监听器启动成功返回 True，否则返回 False。
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
            return True
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 启动永久监听失败: {e}")
            return False
    
    def _permanent_callback_loop(self, server_sock: socket.socket):
        """
        监听 server_sock 上的长度前缀 JSON 回调消息，加入队列，并处理特殊事件。

        此循环接受来自 server_sock 的连接，读取 4 字节大端长度前缀后跟 UTF-8 JSON payload，
        将 payload 解析为 dict，追加到内部消息队列，并：
        - 对 `popup_show` 事件调用已配置的弹窗回调（如果设置）。
        - 收到 `server_started` 或 `disclaimer_closed` 时设置内部 BBC 就绪事件。

        循环继续直到管理器的 `callback_listening` 状态变为 False。
        Socket 超时被忽略（循环继续）；其他异常被记录且循环继续，除非监听已关闭。

        参数:
            server_sock (socket.socket): 用于接受回调连接的绑定/监听服务器 socket。
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
                try:
                    length_bytes = self._recv_all(client_sock, 4)
                    length = struct.unpack('>I', length_bytes)[0]
                    data = self._recv_all(client_sock, length)
                except (ConnectionError, struct.error) as e:
                    mfaalog.debug(f"[BbcConnectionManager] Callback receive failed: {e}")
                    client_sock.close()
                    continue
                
                msg = json.loads(data.decode('utf-8'))
                
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
        """
        从内部队列获取下一条回调消息，最多等待给定的超时时间。

        参数:
            timeout (float): 等待消息的最大秒数。

        返回:
            dict 或 None: 如果在超时前有消息可用则返回下一条消息字典，否则返回 None。
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._queue_lock:
                if self._message_queue:
                    return self._message_queue.pop(0)
            time.sleep(0.1)  # 缩短为0.1秒，提高响应速度
        return None
    
    def get_messages_by_title(self, title_keyword: str, timeout: float = 2.0) -> list:
        """
        收集 `popup_title` 包含给定关键字的弹窗消息并返回。

        参数:
            title_keyword (str): 在每条消息的 `popup_title` 中搜索的子字符串。
            timeout (float): 在返回前等待匹配消息的最大秒数（如果找到匹配可能更早返回）。

        返回:
            list: 匹配的消息字典列表；如果在 `timeout` 内未找到匹配则返回空列表。
        """
        messages = []
        start_time = time.time()

        while time.time() - start_time < timeout:
            with self._queue_lock:
                for msg in self._message_queue[:]:
                    # Only process popup events (skip non-popup events like server_started, disclaimer_closed)
                    popup_title = msg.get('popup_title', '')
                    # Skip messages that are not popup events (no popup_title or event is not popup_show)
                    if not popup_title and msg.get('event') != 'popup_show':
                        continue
                    if title_keyword in popup_title:
                        messages.append(msg)
                        self._message_queue.remove(msg)

            if messages:
                break
            time.sleep(0.1)  # 缩短为0.1秒，提高响应速度

        return messages
    
    def set_popup_callback(self, callback):
        """
        设置收到 BBC 弹窗事件时调用的函数。

        参数:
            callback (callable): 接受单个 dict 参数的函数——解析后的弹窗消息（例如包含 'event'、'popup_title' 等键）。
        """
        self._popup_callback = callback
        mfaalog.info("[BbcConnectionManager] 弹窗回调已设置")
    
    def connect_tcp(self, timeout: int = 10) -> bool:
        """
        确保管理器与 BBC 命令端口有可用的 TCP 连接。

        如果管理器已标记为 connected，则验证连接是否仍然可用；否则尝试打开新的 TCP socket 到 BBC_TCP_HOST:BBC_TCP_PORT 并更新管理器的连接状态。

        参数:
            timeout (int): 用于连接和验证的 socket 级别超时秒数。

        返回:
            bool: 此调用后有可用连接返回 `true`，否则返回 `false`。
        """
        with self._state_lock:
            if self._state['connected'] and self._tcp_sock:
                # 直接用 socket 探针测试连接，避免通过 send_command() 重入 _state_lock
                if self._probe_socket():
                    return True
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

    def _probe_socket(self) -> bool:
        """
        用 socket 探针直接测试现有连接是否可用，不经过 send_command()（避免重入 _state_lock）。

        发送一个 get_status JSON 命令，接收响应，根据是否成功返回 True/False。
        """
        sock = self._tcp_sock
        data = {'cmd': 'get_status', 'args': {}}
        try:
            msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
            msg_with_len = len(msg).to_bytes(4, 'big') + msg
            sock.sendall(msg_with_len)

            original_timeout = sock.gettimeout()
            sock.settimeout(2)

            length_bytes = self._recv_all(sock, 4)
            length = struct.unpack('>I', length_bytes)[0]
            response_data = self._recv_all(sock, length)

            sock.settimeout(original_timeout)
            result = json.loads(response_data.decode('utf-8'))
            return result.get('success', False)
        except Exception as e:
            mfaalog.debug(f"[BbcConnectionManager] Socket 探针失败: {e}")
            return False
    
    def disconnect_tcp(self):
        """
        关闭管理器与 BBC 的 TCP 连接并更新内部连接状态。

        获取管理器状态锁，关闭底层 TCP socket（如果存在），清除 socket 引用，并将连接状态标记为已断开。
        """
        with self._state_lock:
            self._disconnect_tcp()
    
    def _disconnect_tcp(self):
        """
        关闭并清理内部 TCP socket，将管理器标记为已断开。

        此方法关闭存储的 TCP socket，将 socket 引用设为 None，并将内部 `connected` 状态设为 False。
        关闭 socket 时发生的异常被抑制。
        应在持有实例锁的情况下调用以避免竞争条件。
        """
        if self._tcp_sock:
            try:
                self._tcp_sock.close()
            except Exception:
                pass
            self._tcp_sock = None
            self._state['connected'] = False
    
    def send_command(self, cmd: str, args: dict = None, timeout: int = 10) -> dict:
        """
        发送命令到 BBC TCP 服务器并返回解析后的 JSON 响应。

        命令及其参数被编码为 JSON payload，通过管理器已建立的 TCP socket 发送。
        函数最多等待 `timeout` 秒来接收来自服务器的长度前缀 JSON 响应，并返回解码后的字典。

        参数:
            cmd (str): 要发送的命令名称。
            args (dict, 可选): 命令参数；如果省略则视为空字典。
            timeout (int): 等待响应的最大秒数。

        返回:
            dict: 服务器响应的 JSON 解码字典。出错时返回 `{'success': False, 'error': <message>}` 形式的字典。
        """
        # 先用 _state_lock 检查连接状态
        with self._state_lock:
            if not self._tcp_sock or not self._state['connected']:
                return {'success': False, 'error': 'Not connected'}
            sock = self._tcp_sock

        # 用独立的 _socket_lock 保护发送+接收的完整交换过程，避免多线程交织
        with self._socket_lock:
            data = {'cmd': cmd, 'args': args or {}}
            try:
                msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
                msg_with_len = len(msg).to_bytes(4, 'big') + msg
                sock.sendall(msg_with_len)

                # 接收响应
                original_timeout = sock.gettimeout()
                sock.settimeout(timeout)

                length_bytes = self._recv_all(sock, 4)
                length = struct.unpack('>I', length_bytes)[0]
                response_data = self._recv_all(sock, length)

                sock.settimeout(original_timeout)
                return json.loads(response_data.decode('utf-8'))
            except socket.timeout:
                return {'success': False, 'error': f'Timeout (cmd={cmd})'}
            except ConnectionError as e:
                mfaalog.error(f"[BbcConnectionManager] 连接失败: {e}")
                return {'success': False, 'error': str(e)}
            except Exception as e:
                mfaalog.error(f"[BbcConnectionManager] 发送命令失败: {e}")
                return {'success': False, 'error': str(e)}
    
    def _recv_all(self, sock: socket.socket, n: int) -> bytes:
        """
        从给定 socket 精确接收 n 字节。

        参数:
            sock (socket.socket): 要读取的已连接 socket。
            n (int): 要读取的字节数；函数阻塞直到接收到这么多字节。

        返回:
            bytes: 长度为 `n` 的字节数据。

        异常:
            ConnectionError: socket 关闭（收到空数据包）或 recv 错误时抛出。
        """
        data = b''
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
                if not packet:
                    raise ConnectionError("Socket connection closed (empty packet received)")
                data += packet
            except socket.error as e:
                raise ConnectionError(f"Socket recv failed: {e}") from e
        return data
    
    def is_connected(self) -> bool:
        """
        返回管理器的 BBC TCP 命令连接当前是否可用。

        如果现有 socket 的短探测失败，管理器将关闭 socket 并将其标记为已断开。

        返回:
            bool: TCP 连接可用返回 True，否则返回 False。
        """
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
                length = struct.unpack('>I', length_bytes)[0]
                response_data = self._recv_all(self._tcp_sock, length)

                self._tcp_sock.settimeout(original_timeout)
                return True
            except Exception:
                self._disconnect_tcp()
                return False
    
    def ensure_connected(self, timeout: int = 5) -> bool:
        """
        确保管理器与 BBC 有活动的 TCP 连接，必要时重连。

        参数:
            timeout (int): 尝试重连时使用的连接超时秒数。

        返回:
            bool: 此调用后连接成功返回 `True`，否则返回 `False`。
        """
        if self.is_connected():
            mfaalog.debug("[BbcConnectionManager] 连接有效")
            return True
        
        mfaalog.info("[BbcConnectionManager] 连接失效，尝试重连...")
        return self.connect_tcp(timeout=timeout)
    
    def clear_message_queue(self):
        """
        清空内部队列中接收的回调消息。

        此操作是线程安全的，移除所有待处理消息以便后续调用 get_message 或 get_messages_by_title 只看到新接收的消息。
        """
        with self._queue_lock:
            self._message_queue.clear()
        mfaalog.debug("[BbcConnectionManager] 消息队列已清空")
    
    # ==================== BBC 进程管理 ====================
    
    def _find_bbc_process(self):
        """
        查找命令行包含 'BBchannel.exe' 的运行中 BBC 进程。

        遍历系统进程，返回命令行包含 'BBchannel.exe' 的第一个进程。
        处理单个进程的临时访问/终止错误；意外错误被记录并返回 None。

        返回:
            psutil.Process 或 None: 找到匹配的进程对象，否则返回 `None`。
        """
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
    
    def find_bbc_process(self):
        """
        查找命令行包含 'BBchannel.exe' 的运行中 BBC 进程。

        返回:
            psutil.Process: 找到匹配的进程对象，否则返回 `None`。
        """
        return self._find_bbc_process()
    
    def _kill_bbc_process(self, proc=None):
        """
        终止正在运行的 BBC 子进程，关闭其重定向的 stdout/stderr 文件句柄，并清除相关状态字段。

        如果提供了 `proc`，则针对该进程；否则使用管理器存储的 `bbc_process`。
        此方法将尝试优雅终止，如果在超时内进程未退出则回退到 kill，
        关闭任何关联的 stdout/stderr 文件对象，并将管理器的 `bbc_process`、`bbc_stdout_file` 和 `bbc_stderr_file` 状态条目设为 None。

        参数:
            proc (subprocess.Popen | None): 要终止的可选子进程实例。如果为 None，则使用管理器状态中存储的进程。
        """
        if proc is None:
            with self._state_lock:
                proc = self._state.get('bbc_process')
                stdout_file = self._state.get('bbc_stdout_file')
                stderr_file = self._state.get('bbc_stderr_file')
        else:
            with self._state_lock:
                stdout_file = self._state.get('bbc_stdout_file')
                stderr_file = self._state.get('bbc_stderr_file')

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
        except Exception as e:
            mfaalog.warning(f"[BbcConnectionManager] 终止进程失败: {e}")
        finally:
            # 关闭文件句柄
            if stdout_file:
                try:
                    stdout_file.close()
                    mfaalog.debug("[BbcConnectionManager] stdout文件已关闭")
                except Exception as e:
                    mfaalog.warning(f"[BbcConnectionManager] 关闭stdout文件失败: {e}")
            if stderr_file:
                try:
                    stderr_file.close()
                    mfaalog.debug("[BbcConnectionManager] stderr文件已关闭")
                except Exception as e:
                    mfaalog.warning(f"[BbcConnectionManager] 关闭stderr文件失败: {e}")
            # 清理状态
            with self._state_lock:
                self._state['bbc_process'] = None
                self._state['bbc_stdout_file'] = None
                self._state['bbc_stderr_file'] = None
    
    def _launch_bbc(self):
        """
        启动 BBC 可执行文件作为子进程，并将进程记录在管理器状态中。

        启动 BBchannel.exe（路径由 BBC_EXE_PATH 定义），将 stdout/stderr 重定向到
        可执行目录中的 bbc_stdout.log/bbc_stderr.log，成功时将 subprocess.Popen 对象
        存储在 self._state['bbc_process'] 中。

        返回:
            subprocess.Popen 或 None: 启动成功返回启动的进程对象，如果可执行文件缺失或启动失败返回 `None`。
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
                self._state['bbc_stdout_file'] = stdout_file
                self._state['bbc_stderr_file'] = stderr_file

            return proc
        except Exception as e:
            mfaalog.error(f"[BbcConnectionManager] 启动BBC失败: {e}")
            # 确保在失败时关闭文件句柄
            try:
                if 'stdout_file' in locals():
                    stdout_file.close()
                if 'stderr_file' in locals():
                    stderr_file.close()
            except Exception as cleanup_error:
                mfaalog.debug(f"[BbcConnectionManager] 清理文件句柄时出错: {cleanup_error}")
            return None
    
    def _wait_for_bbc_ready(self, timeout: int = 30) -> bool:
        """
        等待 BBC 就绪事件被设置。

        阻塞最多 `timeout` 秒，等待指示 BBC 已就绪的内部就绪事件。

        参数:
            timeout (int): 等待就绪事件的最大秒数。

        返回:
            bool: 在 `timeout` 内就绪事件被设置则返回 `True`，否则返回 `False`。
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
        """
        尝试将 BBC 连接到模拟器并验证模拟器报告的状态。

        参数:
            connect_cmd (str): 请求模拟器连接的 BBC 命令名称。
            connect_args (dict): 命令参数。如果 `connect_args.get('mode') == 'auto'`，则不发送命令，方法短暂等待后返回成功。
            timeout (int): 连接命令 RPC 的超时秒数。

        返回:
            bool: 操作后模拟器报告为可用或已连接则返回 `true`，否则返回 `false`。
        """
        try:
            # auto模式不发送连接命令，轮询连接状态直到成功或超时
            if connect_args.get('mode') == 'auto':
                mfaalog.info("[BbcConnectionManager] Auto模式，轮询连接状态...")
                poll_timeout = 30
                poll_interval = 1
                start_time = time.time()
                while time.time() - start_time < poll_timeout:
                    try:
                        status_result = self.send_command('get_connection', {}, timeout=5)
                        device_available = status_result.get('available', False)
                        device_connected = status_result.get('connected', False)
                        if device_available or device_connected:
                            mfaalog.info(f"[BbcConnectionManager] Auto模式连接成功 (available={device_available}, connected={device_connected})")
                            return True
                        mfaalog.debug(f"[BbcConnectionManager] Auto模式轮询中 (available={device_available}, connected={device_connected})")
                    except Exception as e:
                        mfaalog.debug(f"[BbcConnectionManager] Auto模式轮询异常: {e}")
                    time.sleep(poll_interval)
                mfaalog.error(f"[BbcConnectionManager] Auto模式连接超时 ({poll_timeout}s)")
                return False
            
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
        重启 BBC 应用并使用提供的命令尝试连接模拟器。

        每次尝试（最多 `max_retries` 次），管理器：清空消息队列和就绪事件，确保任何现有 BBC 进程已终止，
        启动新的 BBC 进程，等待其就绪，建立 TCP 命令连接，然后发出模拟器连接过程。
        如果任何步骤失败则重试直到达到 `max_retries`。

        参数:
            connect_cmd (str): 要发送给 BBC 的模拟器连接命令名称（例如 'connect_ld'、'connect_mumu'、'connect_adb' 或 'auto'）。
            connect_args (dict): 连接命令的参数；内容取决于 `connect_cmd`（例如路径、IP、模式）。
            max_retries (int): 放弃前的最大重启和连接尝试次数。

        返回:
            bool: BBC 重启成功且模拟器连接成功则返回 `True`，否则返回 `False`。
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
        """
        返回管理器内部状态的浅拷贝。

        返回:
            dict: 内部状态字典的浅拷贝（例如包含 'connected'、'callback_listening'、'bbc_process'）。
        """
        with self._state_lock:
            return self._state.copy()
    
    def get_last_popup(self) -> Optional[dict]:
        """
        获取存储在管理器状态中的最近一次弹窗消息。

        返回:
            dict: 最近一次弹窗消息字典，如果没有弹窗记录则返回 `None`。
        """
        with self._state_lock:
            return self._state.get('last_popup')
    
    def check_emulator_params_match(self, connect_cmd: str, expected_args: dict, actual_params: dict) -> bool:
        """
        判断模拟器报告的参数是否满足指定连接命令的预期参数。

        执行以下检查：
        - connect_mumu: 比较预期 'path' -> 实际 'mumu_path'、'index' -> 'emulator_index'、'pkg' -> 'pkg' 和 'app_index' -> 'app_index'。
        - connect_ld: 比较预期 'path' -> 实际 'ld_path' 和 'index' -> 'emulator_index'。
        - connect_adb: 比较预期 'ip' -> 实际 'ip'。
        - auto: 如果有任何实际参数存在则认为匹配。

        参数:
            connect_cmd (str): 连接命令类型（例如 "connect_mumu"、"connect_ld"、"connect_adb"、"auto"）。
            expected_args (dict): 连接命令的预期参数值。
            actual_params (dict): 模拟器报告的要验证的参数。

        返回:
            bool: 给定 `connect_cmd` 的实际参数满足预期则返回 `true`，否则返回 `false`。
        """
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
        """
        关闭任何活动的 BBC 命令 TCP 连接，同时保留永久回调监听器。

        这确保管理器的 TCP 命令 socket 已关闭，相关网络资源已释放。
        """
        self.disconnect_tcp()
        mfaalog.info("[BbcConnectionManager] TCP连接已清理")


# 进程级单例（每个 agent 进程一个实例）
_manager_instance = None
_manager_lock = threading.Lock()

def get_manager() -> BbcConnectionManager:
    """
    获取或创建进程级 BbcConnectionManager 单例。

    返回:
        BbcConnectionManager: 此进程的缓存管理器实例。

    异常:
        RuntimeError: 如果管理器构造失败（例如端口清理或监听器启动失败）。
    """
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            # Double-checked locking
            if _manager_instance is None:
                try:
                    _manager_instance = BbcConnectionManager()
                except Exception as e:
                    mfaalog.error(f"[get_manager] Failed to create BbcConnectionManager: {e}")
                    # Do not cache a failed instance
                    raise
    return _manager_instance