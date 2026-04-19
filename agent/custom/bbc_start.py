import json
import os
import time
import socket
import struct
import subprocess
import logging
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
            print(f"[TCP] 已连接到 BBC TCP 服务 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            print(f"[TCP] 连接失败: {e}")
            return False
    
    def send_command(self, cmd: str, args: dict = None, timeout: int = None) -> dict:
        """发送命令并同步等待响应"""
        if not self.sock:
            return {'success': False, 'error': 'Not connected'}
        
        data = {'cmd': cmd, 'args': args or {}}
        try:
            # 发送命令
            msg = json.dumps(data, ensure_ascii=False).encode('utf-8')
            msg_with_len = len(msg).to_bytes(4, 'big') + msg
            self.sock.sendall(msg_with_len)
            
            # 同步接收响应
            if timeout is not None:
                self.sock.settimeout(timeout)
            else:
                self.sock.settimeout(None)  # 阻塞模式，无超时
            
            length_bytes = self._recv_all(4)
            if not length_bytes:
                return {'success': False, 'error': 'Connection closed'}
            
            length = struct.unpack('>I', length_bytes)[0]
            response_data = self._recv_all(length)
            if not response_data:
                return {'success': False, 'error': 'No response data'}
            
            return json.loads(response_data.decode('utf-8'))
        except socket.timeout:
            return {'success': False, 'error': 'Timeout waiting for response'}
        except Exception as e:
            print(f"[TCP] 发送命令失败: {e}")
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
    """启动BBC进程、配置连接参数并等待TCP连接"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("启动bbc")
            if not node_data:
                print(f"[StartBbc] 错误：无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取连接相关参数
            connect = attach_data.get('connect', 'auto')
            
            # 将连接类型转换为 BBC 服务端命令
            connect_cmd = {
                'mumu': 'connect_mumu',
                'ld': 'connect_ld', 
                'adb': 'connect_adb',
                'connect_mumu': 'connect_mumu',
                'connect_ld': 'connect_ld',
                'connect_adb': 'connect_adb'
            }.get(connect, connect)
            
            mumu_path = attach_data.get('mumu_path', '')
            mumu_index = attach_data.get('mumu_index', 0)
            mumu_pkg = attach_data.get('mumu_pkg', 'com.bilibili.fatego')
            mumu_app_index = attach_data.get('mumu_app_index', 0)
            ld_path = attach_data.get('ld_path', '')
            ld_index = attach_data.get('ld_index', 0)
            manual_port = attach_data.get('manual_port', '')
            
            print(f"[StartBbc] 连接参数: connect={connect}, connect_cmd={connect_cmd}, manual_port={manual_port}")
            print(f"[StartBbc] MuMu参数: path={mumu_path}, index={mumu_index}, pkg={mumu_pkg}, app_index={mumu_app_index}")
            print(f"[StartBbc] LD参数: path={ld_path}, index={ld_index}")
            
            # 步骤1: 启动BBC
            print("[StartBbc] 步骤1: 启动BBC...")
            
            # 检查BBC可执行文件
            if not os.path.exists(BBC_EXE_PATH):
                print(f"[StartBbc] BBC可执行文件不存在: {BBC_EXE_PATH}")
                return CustomAction.RunResult(success=False)
            
            # 启动 BBC 进程
            print("[StartBbc] 启动 BBC 进程...")
            print(f"[StartBbc] BBC 路径：{BBC_EXE_PATH}")
            logger.info(f"[StartBbc] 启动 BBC 进程，路径：{BBC_EXE_PATH}")
                        
            # 切换到 BBC 所在目录再启动
            bbc_dir = os.path.dirname(BBC_EXE_PATH)
            _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
            _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
            
            print(f"[StartBbc] 调试模式: {_is_debug}")
            print(f"[StartBbc] CreationFlags: {_creation_flags}")
            print(f"[StartBbc] 工作目录: {bbc_dir}")
            
            # 重定向输出到文件以便调试
            stdout_file = open(os.path.join(bbc_dir, 'bbc_stdout.log'), 'w', encoding='utf-8')
            stderr_file = open(os.path.join(bbc_dir, 'bbc_stderr.log'), 'w', encoding='utf-8')
            
            proc = subprocess.Popen(
                [BBC_EXE_PATH],
                cwd=bbc_dir,
                creationflags=_creation_flags,
                stdout=stdout_file,
                stderr=stderr_file
            )
            logger.info(f"[StartBbc] 已启动进程，PID: {proc.pid}")
            print(f"[StartBbc] BBC 进程已启动，PID: {proc.pid}")
                        
            print("[StartBbc] BBC 启动命令已发送")
            
            # 检查进程是否立即退出
            time.sleep(2)  # 增加等待时间，让BBC有机会初始化
            exit_code = proc.poll()
            if exit_code is not None:
                # 进程已退出，读取错误输出
                stdout_file.close()
                stderr_file.close()
                
                # 读取日志文件内容
                try:
                    with open(os.path.join(bbc_dir, 'bbc_stdout.log'), 'r', encoding='utf-8') as f:
                        stdout_content = f.read()[:500]
                except:
                    stdout_content = ''
                
                try:
                    with open(os.path.join(bbc_dir, 'bbc_stderr.log'), 'r', encoding='utf-8') as f:
                        stderr_content = f.read()[:500]
                except:
                    stderr_content = ''
                
                print(f"[StartBbc] BBC 进程异常退出！退出码: {exit_code}")
                if stdout_content:
                    print(f"[StartBbc] stdout:\n{stdout_content}")
                if stderr_content:
                    print(f"[StartBbc] stderr:\n{stderr_content}")
                logger.error(f"[StartBbc] BBC 进程异常退出，退出码: {exit_code}")
                logger.error(f"[StartBbc] stderr: {stderr_content}")
                return CustomAction.RunResult(success=False)
            
            # 步骤2: 连接TCP
            print("[StartBbc] 步骤2: 连接 TCP 服务...")
            tcp_client = BbcTcpClient()
            
            # 持续监控进程状态
            connect_start_time = time.time()
            connect_timeout = 30
            connected = False
            
            while time.time() - connect_start_time < connect_timeout:
                # 检查进程是否还在运行
                if proc.poll() is not None:
                    exit_code = proc.poll()
                    stdout_file.close()
                    stderr_file.close()
                    
                    # 读取日志文件
                    try:
                        with open(os.path.join(bbc_dir, 'bbc_stderr.log'), 'r', encoding='utf-8') as f:
                            stderr_content = f.read()[:500]
                    except:
                        stderr_content = ''
                    
                    print(f"[StartBbc] BBC 进程在TCP连接期间退出！退出码: {exit_code}")
                    if stderr_content:
                        print(f"[StartBbc] stderr:\n{stderr_content}")
                    logger.error(f"[StartBbc] BBC 进程在TCP连接期间退出，退出码: {exit_code}")
                    return CustomAction.RunResult(success=False)
                
                if tcp_client.connect(timeout=1):
                    connected = True
                    break
                time.sleep(0.2)
            
            if not connected:
                print("[StartBbc] TCP 连接失败，超时")
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                        if proc.poll() is None:
                            proc.kill()
                except:
                    pass
                return CustomAction.RunResult(success=False)
            
            print("[StartBbc] TCP 连接成功")
            
            # 步骤2.5: 等待免责声明自动关闭
            print("[StartBbc] 步骤2.5: 等待免责声明处理...")
            # BBC 服务端已实现免责声明自动关闭机制（2秒后自动确认）
            # 这里只需等待足够时间确保关闭完成
            time.sleep(3)
            print("[StartBbc] 免责声明处理完成")
            
            # 步骤3: 发送连接配置并执行连接
            print(f"[StartBbc] 步骤3: 执行模拟器连接，命令: {connect_cmd}...")
            connect_args = {
                'path': mumu_path if connect_cmd == 'connect_mumu' else ld_path,
                'index': int(mumu_index) if connect_cmd == 'connect_mumu' else int(ld_index),
                'pkg': mumu_pkg if connect_cmd == 'connect_mumu' else None,
                'app_index': int(mumu_app_index) if connect_cmd == 'connect_mumu' else None,
                'ip': manual_port if connect_cmd == 'connect_adb' else None
            }
            connect_result = tcp_client.send_command(connect_cmd, connect_args, timeout=30)
            
            if not connect_result.get('success'):
                error_msg = connect_result.get('error', '未知错误')
                print(f"[StartBbc] 连接失败: {error_msg}")
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                        if proc.poll() is None:
                            proc.kill()
                except:
                    pass
                tcp_client.stop()
                return CustomAction.RunResult(success=False)
            
            # 步骤4: 验证模拟器连接状态
            print("[StartBbc] 步骤4: 验证模拟器连接状态...")
            time.sleep(1)  # 等待连接稳定
            
            verify_result = tcp_client.send_command('get_status', {}, timeout=5)
            if not verify_result.get('success'):
                print(f"[StartBbc] 获取状态失败: {verify_result.get('error')}")
                tcp_client.stop()
                return CustomAction.RunResult(success=False)
            
            # 注意：BBC 服务端 get_status 不返回 device_connected 字段
            # 所以只要连接命令返回 success=True，就认为连接成功
            print("[StartBbc] 模拟器连接验证成功")
            
            tcp_client.stop()
            print("[StartBbc] 连接成功")
            return CustomAction.RunResult(success=True)
            
        except Exception as e:
            print(f"[StartBbc] 启动BBC出错: {e}")
            import traceback
            traceback.print_exc()
            return CustomAction.RunResult(success=False)
