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
from typing import Optional, Dict, Any

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
    """BBC 连接管理器 - 单例"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._tcp_sock: Optional[socket.socket] = None
        self._callback_server: Optional[socket.socket] = None
        self._callback_thread: Optional[threading.Thread] = None
        self._message_queue = []  # 消息队列
        self._queue_lock = threading.Lock()
        self._popup_callback = None  # 弹窗回调函数
        self._state = {
            'connected': False,
            'callback_listening': False,
            'bbc_process': None,  # BBC 进程对象
        }
        self._state_lock = threading.Lock()
        
        self._initialized = True
        logger.info("[BbcConnectionManager] 初始化完成")
        
        # 自动启动回调监听
        self._start_permanent_listener()
    
    def _start_permanent_listener(self):
        """启动永久回调监听（后台线程）"""
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
            
            logger.info(f"[BbcConnectionManager] 永久回调监听已启动 on port {BBC_CALLBACK_PORT}")
        except Exception as e:
            logger.error(f"[BbcConnectionManager] 启动永久监听失败: {e}")
    
    def _permanent_callback_loop(self, server_sock: socket.socket):
        """永久回调监听主循环 - 将消息放入队列"""
        logger.info("[BbcConnectionManager] 永久回调监听循环开始")
        
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
                logger.debug(f"[BbcConnectionManager] 收到回调: {msg}")
                
                # 放入消息队列
                with self._queue_lock:
                    self._message_queue.append(msg)
                
                # 触发弹窗回调（如果是弹窗事件）
                if msg.get('event') == 'popup_show' and self._popup_callback:
                    try:
                        self._popup_callback(msg)
                    except Exception as e:
                        logger.error(f"[BbcConnectionManager] 弹窗回调执行失败: {e}")
                
                client_sock.close()
            except socket.timeout:
                continue
            except Exception as e:
                with self._state_lock:
                    if not self._state['callback_listening']:
                        break
                logger.warning(f"[BbcConnectionManager] 回调接收异常: {e}")
                continue
        
        logger.info("[BbcConnectionManager] 永久回调监听循环结束")
    
    def get_message(self, timeout: float = 1.0) -> Optional[dict]:
        """从消息队列获取一条消息（阻塞等待）"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            with self._queue_lock:
                if self._message_queue:
                    return self._message_queue.pop(0)
            time.sleep(2)
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
            time.sleep(2)
        
        return messages
    
    def set_popup_callback(self, callback):
        """设置弹窗回调函数"""
        self._popup_callback = callback
        logger.info("[BbcConnectionManager] 弹窗回调已设置")
    
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
            
            logger.info(f"[BbcConnectionManager] TCP 连接成功 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            logger.error(f"[BbcConnectionManager] TCP 连接失败: {e}")
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
            logger.error(f"[BbcConnectionManager] 发送命令失败: {e}")
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
            logger.debug("[BbcConnectionManager] 连接有效")
            return True
        
        logger.info("[BbcConnectionManager] 连接失效，尝试重连...")
        return self.connect_tcp(timeout=timeout)
    
    def clear_message_queue(self):
        """清空消息队列"""
        with self._queue_lock:
            self._message_queue.clear()
        logger.debug("[BbcConnectionManager] 消息队列已清空")
    
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
            logger.warning(f"[BbcConnectionManager] 查找进程失败: {e}")
            return None
    
    def _kill_bbc_process(self, proc=None):
        """终止BBC进程"""
        if proc is None:
            with self._state_lock:
                proc = self._state.get('bbc_process')
        
        try:
            if proc and proc.is_running():
                logger.info(f"[BbcConnectionManager] 终止BBC进程 PID: {proc.pid}")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
                logger.info("[BbcConnectionManager] BBC进程已终止")
                with self._state_lock:
                    self._state['bbc_process'] = None
        except Exception as e:
            logger.warning(f"[BbcConnectionManager] 终止进程失败: {e}")
    
    def _launch_bbc(self):
        """启动BBC进程"""
        if not os.path.exists(BBC_EXE_PATH):
            logger.error(f"[BbcConnectionManager] BBC可执行文件不存在: {BBC_EXE_PATH}")
            return None
        
        bbc_dir = os.path.dirname(BBC_EXE_PATH)
        _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
        _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
        
        logger.info(f"[BbcConnectionManager] 启动BBC: {BBC_EXE_PATH}")
        logger.info(f"[BbcConnectionManager] 调试模式: {_is_debug}, 工作目录: {bbc_dir}")
        
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
            logger.info(f"[BbcConnectionManager] BBC进程已启动，PID: {proc.pid}")
            
            with self._state_lock:
                self._state['bbc_process'] = proc
            
            return proc
        except Exception as e:
            logger.error(f"[BbcConnectionManager] 启动BBC失败: {e}")
            return None
    
    def _wait_for_bbc_ready(self, timeout: int = 30) -> bool:
        """从消息队列等待 BBC 就绪信号"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            msg = self.get_message(timeout=0.5)
            if msg:
                event = msg.get('event', '')
                if event in ['server_started', 'disclaimer_closed']:
                    logger.info(f"[BbcConnectionManager] BBC 就绪事件: {event}")
                    return True
        
        logger.warning(f"[BbcConnectionManager] 等待 BBC 就绪超时 ({timeout}s)")
        return False
    
    # ==================== 模拟器连接 ====================
    
    def connect_emulator(self, connect_cmd: str, connect_args: dict, timeout: int = 30) -> bool:
        """连接模拟器（封装 BBC 命令）"""
        try:
            # auto模式不发送连接命令，直接等待
            if connect_args.get('mode') == 'auto':
                logger.info("[BbcConnectionManager] Auto模式，等待BBC自动连接...")
                time.sleep(5)
                return True
            
            # 先等待 BBC UI 完全就绪
            logger.info("[BbcConnectionManager] 等待 BBC UI 完全就绪...")
            time.sleep(5)
            
            # 发送连接命令
            logger.info(f"[BbcConnectionManager] 执行连接命令: {connect_cmd}, 参数: {connect_args}")
            result = self.send_command(connect_cmd, connect_args, timeout=timeout)
            
            if not result.get('success'):
                error_msg = result.get('error', '未知错误')
                logger.error(f"[BbcConnectionManager] 连接失败: {error_msg}")
                return False
            
            logger.info("[BbcConnectionManager] 连接命令执行成功")
            time.sleep(5)
            
            # 验证连接状态
            status_result = self.send_command('get_connection', {}, timeout=5)
            device_available = status_result.get('available', False)
            device_connected = status_result.get('connected', False)
            
            if device_available or device_connected:
                logger.info(f"[BbcConnectionManager] 模拟器连接成功 (available={device_available}, connected={device_connected})")
                return True
            else:
                logger.warning(f"[BbcConnectionManager] 模拟器未连接 (available={device_available}, connected={device_connected})")
                return False
        except Exception as e:
            logger.error(f"[BbcConnectionManager] 连接异常: {e}")
            return False
    
    # ==================== 完整重启流程 ====================
    
    def restart_bbc_and_connect(self, connect_cmd: str, connect_args: dict, max_retries: int = 5) -> bool:
        """重启 BBC 并连接模拟器（完整流程）"""
        logger.info(f"[BbcConnectionManager] ========== 开始重启 BBC ==========")
        
        for attempt in range(1, max_retries + 1):
            logger.info(f"[BbcConnectionManager] 第{attempt}次启动尝试")
            
            # 1. 杀掉旧进程
            self._kill_bbc_process()
            time.sleep(5)
            
            # 2. 启动新进程
            bbc_proc = self._launch_bbc()
            if not bbc_proc:
                logger.error(f"[BbcConnectionManager] BBC进程启动失败 (尝试 {attempt})")
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 3. 等待 BBC 就绪
            logger.info("[BbcConnectionManager] 等待BBC就绪...")
            ready = self._wait_for_bbc_ready(timeout=30)
            if not ready:
                logger.warning(f"[BbcConnectionManager] BBC就绪超时 (尝试 {attempt})")
                self._kill_bbc_process(bbc_proc)
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 4. 建立 TCP 连接
            logger.info("[BbcConnectionManager] BBC已就绪，建立TCP连接...")
            if not self.connect_tcp(timeout=10):
                logger.warning(f"[BbcConnectionManager] TCP连接失败 (尝试 {attempt})")
                self._kill_bbc_process(bbc_proc)
                if attempt < max_retries:
                    time.sleep(5)
                    continue
                else:
                    return False
            
            # 5. 连接模拟器
            logger.info("[BbcConnectionManager] 连接模拟器...")
            if self.connect_emulator(connect_cmd, connect_args, timeout=30):
                logger.info("[BbcConnectionManager] BBC重启并连接成功")
                return True
            else:
                logger.warning(f"[BbcConnectionManager] 模拟器连接失败 (尝试 {attempt})")
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
            return self._state['last_popup']
    
    def cleanup(self):
        """清理所有资源（不关闭永久监听）"""
        self.disconnect_tcp()
        logger.info("[BbcConnectionManager] TCP连接已清理")


# 全局实例
bbc_manager = BbcConnectionManager()
