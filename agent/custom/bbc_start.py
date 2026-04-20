import json
import os
import time
import socket
import struct
import subprocess
import logging
import psutil
import threading
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

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


# BBC TCP 配置
BBC_TCP_HOST = "127.0.0.1"
BBC_TCP_PORT = 25001
BBC_CALLBACK_PORT = 25002

# 固定 BBC 路径
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BBC_PATH = os.path.join(AGENT_ROOT, '..', 'BBchannel')
BBC_EXE_PATH = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
BBC_EXE_PATH = os.path.abspath(BBC_EXE_PATH)


class BbcTcpClient:
    """BBC TCP 客户端 - 同步发送命令并等待响应"""
    
    def __init__(self):
        self.sock = None
    
    def connect(self, timeout: int = 10) -> bool:
        """连接到 BBC TCP 服务"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((BBC_TCP_HOST, BBC_TCP_PORT))
            logger.info(f"[TCP] 已连接到 BBC TCP 服务 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            logger.warning(f"[TCP] 连接失败: {e}")
            return False
    
    def send_command(self, cmd: str, args: dict = None, timeout: int = 10) -> dict:
        """发送命令并同步等待响应"""
        if not self.sock:
            return {'success': False, 'error': 'Not connected'}
        
        data = {'cmd': cmd, 'args': args or {}}
        try:
            # 发送命令
            msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
            msg_with_len = len(msg).to_bytes(4, 'big') + msg
            self.sock.sendall(msg_with_len)
            
            # 接收响应
            self.sock.settimeout(timeout)
            length_bytes = self._recv_all(4)
            if not length_bytes:
                return {'success': False, 'error': 'Connection closed'}
            
            length = struct.unpack('>I', length_bytes)[0]
            response_data = self._recv_all(length)
            if not response_data:
                return {'success': False, 'error': 'No response data'}
            
            return json.loads(response_data.decode('utf-8'))
        except socket.timeout:
            return {'success': False, 'error': f'Timeout waiting for response (cmd={cmd})'}
        except Exception as e:
            logger.error(f"[TCP] 发送命令失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def _recv_all(self, n: int) -> bytes:
        """接收指定字节数的数据"""
        data = b''
        while len(data) < n:
            try:
                packet = self.sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except Exception:
                return None
        return data
    
    def stop(self):
        """关闭连接"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None


@AgentServer.custom_action("start_bbc")
class StartBbc(CustomAction):
    """启动BBC进程、等待就绪并通过TCP连接模拟器"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("启动bbc")
            if not node_data:
                logger.error("[StartBbc] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 调试：打印完整的 attach 数据
            logger.info(f"[StartBbc] 完整 attach 数据: {json.dumps(attach_data, ensure_ascii=False)}")
            
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
            
            logger.info(f"[StartBbc] 连接参数: connect={connect}, cmd={connect_cmd}")
            logger.info(f"[StartBbc] MuMu: path={mumu_path}, index={mumu_index}, pkg={mumu_pkg}")
            logger.info(f"[StartBbc] LD: path={ld_path}, index={ld_index}")
            
            # 步骤1: 检查BBC进程是否存在
            logger.info("[StartBbc] 步骤1: 检查BBC进程...")
            bbc_proc = self._find_bbc_process()
            
            if bbc_proc:
                logger.info(f"[StartBbc] 发现BBC进程，PID: {bbc_proc.pid}")
                # 尝试直接连接TCP
                tcp_client = BbcTcpClient()
                if tcp_client.connect(timeout=3):
                    logger.info("[StartBbc] TCP连接成功，跳过启动步骤")
                    # 直接执行步骤6: 连接模拟器
                    result = self._connect_emulator(tcp_client, connect_cmd, mumu_path, mumu_index, 
                                                   mumu_pkg, mumu_app_index, ld_path, ld_index, manual_port)
                    tcp_client.stop()
                    return result
                else:
                    logger.warning("[StartBbc] TCP连接失败，将重启BBC进程")
                    self._kill_bbc_process(bbc_proc)
                    time.sleep(1)
            
            # 步骤2-7: 启动BBC并重试（最多5次）
            max_retries = 5
            for attempt in range(1, max_retries + 1):
                logger.info(f"[StartBbc] ========== 第{attempt}次启动尝试 ==========")
                
                # 启动BBC进程
                logger.info(f"[StartBbc] 启动BBC进程 (尝试 {attempt}/{max_retries})...")
                bbc_proc = self._launch_bbc()
                if not bbc_proc:
                    logger.error(f"[StartBbc] BBC进程启动失败 (尝试 {attempt})")
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    else:
                        return CustomAction.RunResult(success=False)
                
                # 等待回调端口确认BBC就绪
                logger.info(f"[StartBbc] 等待BBC就绪（监听回调端口）...")
                ready = self._wait_for_callback(timeout=30)
                if not ready:
                    logger.warning(f"[StartBbc] BBC就绪超时 (尝试 {attempt})，终止进程")
                    self._kill_bbc_process(bbc_proc)
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    else:
                        logger.error(f"[StartBbc] 已达到最大重试次数 ({max_retries})，任务失败")
                        return CustomAction.RunResult(success=False)
                
                logger.info("[StartBbc] BBC已就绪，建立TCP连接...")
                
                # 连接TCP
                tcp_client = BbcTcpClient()
                tcp_connected = False
                for retry in range(5):
                    if tcp_client.connect(timeout=3):
                        tcp_connected = True
                        break
                    logger.warning(f"[StartBbc] TCP连接重试 {retry+1}/5")
                    time.sleep(1)
                
                if not tcp_connected:
                    logger.warning(f"[StartBbc] TCP连接失败 (尝试 {attempt})，终止进程")
                    self._kill_bbc_process(bbc_proc)
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    else:
                        logger.error(f"[StartBbc] 已达到最大重试次数 ({max_retries})，任务失败")
                        return CustomAction.RunResult(success=False)
                
                # 连接模拟器
                logger.info(f"[StartBbc] 连接模拟器...")
                result = self._connect_emulator(tcp_client, connect_cmd, mumu_path, mumu_index, 
                                               mumu_pkg, mumu_app_index, ld_path, ld_index, manual_port)
                tcp_client.stop()
                
                if result.success:
                    return CustomAction.RunResult(success=True)
                else:
                    logger.warning(f"[StartBbc] 模拟器连接失败 (尝试 {attempt})，终止进程")
                    self._kill_bbc_process(bbc_proc)
                    if attempt < max_retries:
                        time.sleep(2)
                        continue
                    else:
                        logger.error(f"[StartBbc] 已达到最大重试次数 ({max_retries})，任务失败")
                        return CustomAction.RunResult(success=False)
            
            # 理论上不会到达这里
            logger.error("[StartBbc] 未知错误")
            return CustomAction.RunResult(success=False)
            
        except Exception as e:
            logger.error(f"[StartBbc] 异常: {e}", exc_info=True)
            return CustomAction.RunResult(success=False)
    
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
            logger.warning(f"[StartBbc] 查找进程失败: {e}")
            return None
    
    def _kill_bbc_process(self, proc):
        """终止BBC进程"""
        try:
            if proc and proc.is_running():
                logger.info(f"[StartBbc] 终止BBC进程 PID: {proc.pid}")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
                logger.info("[StartBbc] BBC进程已终止")
        except Exception as e:
            logger.warning(f"[StartBbc] 终止进程失败: {e}")
    
    def _launch_bbc(self):
        """启动BBC进程"""
        if not os.path.exists(BBC_EXE_PATH):
            logger.error(f"[StartBbc] BBC可执行文件不存在: {BBC_EXE_PATH}")
            return None
        
        bbc_dir = os.path.dirname(BBC_EXE_PATH)
        _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
        _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
        
        logger.info(f"[StartBbc] 启动BBC: {BBC_EXE_PATH}")
        logger.info(f"[StartBbc] 调试模式: {_is_debug}, 工作目录: {bbc_dir}")
        
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
            logger.info(f"[StartBbc] BBC进程已启动，PID: {proc.pid}")
            return proc
        except Exception as e:
            logger.error(f"[StartBbc] 启动BBC失败: {e}")
            return None
    
    def _wait_for_callback(self, timeout: int = 30) -> bool:
        """等待回调端口收到BBC就绪信号"""
        server_sock = None
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', BBC_CALLBACK_PORT))
            server_sock.listen(1)
            server_sock.settimeout(2)  # 每次accept超时2秒
            
            logger.info(f"[Callback] 开始监听端口 {BBC_CALLBACK_PORT}")
            start_time = time.time()
            
            while time.time() - start_time < timeout:
                try:
                    client_sock, addr = server_sock.accept()
                    client_sock.settimeout(5)
                    
                    # 接收消息长度
                    length_bytes = self._recv_exact(client_sock, 4)
                    if not length_bytes:
                        client_sock.close()
                        continue
                    
                    length = struct.unpack('>I', length_bytes)[0]
                    data = self._recv_exact(client_sock, length)
                    if not data:
                        client_sock.close()
                        continue
                    
                    msg = json.loads(data.decode('utf-8'))
                    logger.info(f"[Callback] 收到消息: {msg}")
                    
                    # 检查是否是服务器启动或免责声明关闭事件
                    event = msg.get('event', '')
                    if event in ['server_started', 'disclaimer_closed']:
                        logger.info(f"[Callback] BBC就绪事件: {event}")
                        client_sock.close()
                        server_sock.close()
                        return True
                    
                    client_sock.close()
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"[Callback] 接收消息异常: {e}")
                    continue
            
            logger.warning(f"[Callback] 等待超时 ({timeout}s)")
            return False
        except Exception as e:
            logger.error(f"[Callback] 监听失败: {e}")
            return False
        finally:
            if server_sock:
                try:
                    server_sock.close()
                except:
                    pass
    
    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        """从socket接收精确字节数"""
        data = b''
        while len(data) < n:
            try:
                packet = sock.recv(n - len(data))
                if not packet:
                    return None
                data += packet
            except Exception:
                return None
        return data
    
    def _listen_connect_callbacks(self, popup_state: dict):
        """监听连接阶段的回调事件（处理自动连接失败弹窗）"""
        server_sock = None
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', BBC_CALLBACK_PORT))
            server_sock.listen(1)
            server_sock.settimeout(2)
            
            logger.info(f"[Connect Callback] 开始监听端口 {BBC_CALLBACK_PORT}")
            
            while True:
                try:
                    client_sock, addr = server_sock.accept()
                    client_sock.settimeout(5)
                    
                    # 接收消息长度
                    length_bytes = self._recv_exact(client_sock, 4)
                    if not length_bytes:
                        client_sock.close()
                        continue
                    
                    length = struct.unpack('>I', length_bytes)[0]
                    data = self._recv_exact(client_sock, length)
                    if not data:
                        client_sock.close()
                        continue
                    
                    msg = json.loads(data.decode('utf-8'))
                    event = msg.get('event', '')
                    popup_title = msg.get('popup_title', '')
                    popup_id = msg.get('popup_id', '')
                    
                    logger.info(f"[Connect Callback] 收到事件: {event}, 标题: {popup_title}")
                    
                    # 处理自动连接失败
                    if '自动连接失败' in popup_title:
                        logger.warning(f"[Connect Callback] 检测到自动连接失败")
                        popup_state['auto_connect_failed'] = True
                        
                        # 发送响应关闭弹窗
                        tcp_client = BbcTcpClient()
                        if tcp_client.connect(timeout=3):
                            tcp_client.send_command('popup_response', {
                                'popup_id': popup_id,
                                'action': 'ok'
                            }, timeout=5)
                            tcp_client.stop()
                    
                    client_sock.close()
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"[Connect Callback] 接收异常: {e}")
                    continue
        except Exception as e:
            logger.error(f"[Connect Callback] 监听失败: {e}")
        finally:
            if server_sock:
                try:
                    server_sock.close()
                except:
                    pass
    
    def _connect_emulator(self, tcp_client: BbcTcpClient, connect_cmd: str, 
                         mumu_path: str, mumu_index: int, mumu_pkg: str, 
                         mumu_app_index: int, ld_path: str, ld_index: int,
                         manual_port: str) -> CustomAction.RunResult:
        """通过TCP连接模拟器并验证"""
        # 启动回调监听线程
        popup_state = {'auto_connect_failed': False}
        callback_thread = threading.Thread(
            target=self._listen_connect_callbacks,
            args=(popup_state,),
            daemon=True
        )
        callback_thread.start()
        
        try:
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
            
            logger.info(f"[Connect] 执行连接命令: {connect_cmd}, 参数: {connect_args}")
            
            # auto模式不发送连接命令，直接等待
            connect_success = True
            if connect_args.get('mode') != 'auto':
                # 先等待 BBC UI 完全就绪（额外等待2秒）
                logger.info("[Connect] 等待 BBC UI 完全就绪...")
                time.sleep(5)
                
                # 发送连接命令
                connect_result = tcp_client.send_command(connect_cmd, connect_args, timeout=30)
                if not connect_result.get('success'):
                    error_msg = connect_result.get('error', '未知错误')
                    logger.error(f"[Connect] 连接失败: {error_msg}")
                    connect_success = False
                else:
                    logger.info("[Connect] 连接命令执行成功，验证连接状态...")
                    time.sleep(1)
            else:
                logger.info("[Connect] Auto模式，等待BBC自动连接...")
                time.sleep(5)
            
            # 检查是否收到自动连接失败弹窗
            if popup_state['auto_connect_failed']:
                logger.error("[Connect] 检测到自动连接失败弹窗")
                return CustomAction.RunResult(success=False)
            
            # 验证连接状态
            status_result = tcp_client.send_command('get_connection', {}, timeout=5)
            
            # BBC 服务端直接返回连接状态字典，没有 success 字段
            device_available = status_result.get('available', False)
            device_connected = status_result.get('connected', False)
            
            # 最终判断 - 按顺序检查并输出
            # 1. 检查 BBC 是否启动并连接
            if not connect_success:
                logger.error("[StartBbc] BBC启动失败")
                return CustomAction.RunResult(success=False)
            
            logger.info("[StartBbc] BBC启动并连接成功")
            for handler in logger.handlers:
                handler.flush()
            
            # 2. 检查模拟器是否连接成功
            if device_available or device_connected:
                logger.info(f"[Connect] 模拟器连接成功 (available={device_available}, connected={device_connected})")
            else:
                logger.warning(f"[Connect] 模拟器未连接 (available={device_available}, connected={device_connected})")
            for handler in logger.handlers:
                handler.flush()
            
            # 3. 输出详细的连接信息
            final_status = tcp_client.send_command('get_connection', {}, timeout=5)
            logger.info(f"[Connect] BBC连接模拟器详情: {json.dumps(final_status, ensure_ascii=False)}")
            for handler in logger.handlers:
                handler.flush()
            
            return CustomAction.RunResult(success=True)
        finally:
            # 等待回调线程结束（最多1秒）
            callback_thread.join(timeout=1)
