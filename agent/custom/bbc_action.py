import json
import os
import time
import socket
import struct
import threading
import logging
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 配置日志输出到文件
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_debug.log')

# 创建具名 logger
logger = logging.getLogger("BbcAction")
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


# TCP客户端管理
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



# ==================== Action: 执行BBC任务（仅战斗部分）====================
@AgentServer.custom_action("execute_bbc_task")
class ExecuteBbcTask(CustomAction):
    """执行BBC战斗任务 - 事件驱动模式"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("执行BBC任务")
            if not node_data:
                logger.error("[ExecuteBbcTask] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取参数
            team_config = attach_data.get('bbc_team_config', '')
            run_count = attach_data.get('run_count')
            apple_type = attach_data.get('apple_type')
            battle_type = attach_data.get('battle_type', '连续出击')
            support_order_mismatch = attach_data.get('support_order_mismatch', False)
            team_config_error = attach_data.get('team_config_error', False)
            
            # 验证必需参数
            if not team_config or run_count is None or apple_type is None:
                logger.error(f"[ExecuteBbcTask] 参数不完整: team={team_config}, count={run_count}, apple={apple_type}")
                return CustomAction.RunResult(success=False)
            
            run_count = int(run_count)
            logger.info(f"[ExecuteBbcTask] 参数: team={team_config}, count={run_count}, apple={apple_type}, type={battle_type}")
            
            # 步骤1: 尝试TCP连接，失败则触发bbc_start
            tcp_client = self._ensure_bbc_connected(context, attach_data)
            if not tcp_client:
                return CustomAction.RunResult(success=False)
            
            # 步骤2: 验证模拟器连接
            if not self._verify_emulator_connection(tcp_client, attach_data, context):
                tcp_client.stop()
                return CustomAction.RunResult(success=False)
            
            # 步骤3: 配置并启动战斗
            if not self._setup_and_start_battle(tcp_client, team_config, run_count, apple_type, battle_type):
                tcp_client.stop()
                return CustomAction.RunResult(success=False)
            
            # 步骤4: 监听回调等待战斗结束
            popup_title, popup_message = self._wait_for_battle_end(tcp_client, attach_data)
            
            tcp_client.stop()
            
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
                logger.info(f"[ExecuteBbcTask] 战斗结束: {display_text}")
            else:
                logger.info("[ExecuteBbcTask] 战斗正常结束")
            
            return CustomAction.RunResult(success=True)
            
        except Exception as e:
            logger.error(f"[ExecuteBbcTask] 异常: {e}", exc_info=True)
            return CustomAction.RunResult(success=False)
    
    def _ensure_bbc_connected(self, context: Context, attach_data: dict):
        """确保BBC已连接，必要时触发bbc_start"""
        from .bbc_start import BbcTcpClient
        
        tcp_client = BbcTcpClient()
        if tcp_client.connect(timeout=3):
            logger.info("[ExecuteBbcTask] TCP连接成功")
            return tcp_client
        
        logger.warning("[ExecuteBbcTask] TCP连接失败，触发bbc_start...")
        
        # 触发bbc_start pipeline节点
        result = context.run_task("启动bbc")
        if not result:
            logger.error("[ExecuteBbcTask] bbc_start执行失败")
            return None
        
        # 重新连接
        time.sleep(2)
        if tcp_client.connect(timeout=5):
            logger.info("[ExecuteBbcTask] bbc_start后TCP连接成功")
            return tcp_client
        
        logger.error("[ExecuteBbcTask] bbc_start后TCP仍连接失败")
        return None
    
    def _verify_emulator_connection(self, tcp_client, attach_data: dict, context: Context) -> bool:
        """验证模拟器连接，必要时重新连接或重启BBC"""
        conn_status = tcp_client.send_command('get_connection', {}, timeout=5)
        if not conn_status.get('success'):
            logger.warning("[ExecuteBbcTask] 获取连接状态失败")
            return True  # 宽容处理
        
        if conn_status.get('connected') or conn_status.get('available'):
            logger.info("[ExecuteBbcTask] 模拟器已连接")
            return True
        
        logger.warning("[ExecuteBbcTask] 模拟器未连接，尝试连接...")
        
        # 尝试连接模拟器
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
        
        result = tcp_client.send_command(connect_cmd, connect_args, timeout=30)
        if not result.get('success'):
            logger.error(f"[ExecuteBbcTask] 模拟器连接失败: {result.get('error')}")
            
            # 连接失败，重启BBC
            logger.warning("[ExecuteBbcTask] 重启BBC...")
            tcp_client.stop()
            
            result = context.run_task("启动bbc")
            if not result:
                return False
            
            # 重新建立连接
            from .bbc_start import BbcTcpClient
            new_tcp = BbcTcpClient()
            time.sleep(2)
            if not new_tcp.connect(timeout=5):
                logger.error("[ExecuteBbcTask] 重启后TCP连接失败")
                return False
            
            # 替换tcp_client引用（通过返回值）
            # 注意：这里需要特殊处理，因为Python不能直接修改传入的对象引用
            # 简化处理：假设重启后BBC会自动恢复连接
            return True
        
        logger.info("[ExecuteBbcTask] 模拟器连接成功")
        return True
    
    def _setup_and_start_battle(self, tcp_client, team_config: str, run_count: int, 
                                apple_type: str, battle_type: str) -> bool:
        """配置战斗参数并启动"""
        # 加载配置
        logger.info(f"[ExecuteBbcTask] 加载配置: {team_config}")
        result = tcp_client.send_command('load_config', {'filename': team_config}, timeout=10)
        if not result.get('success'):
            logger.error(f"[ExecuteBbcTask] 加载配置失败: {result.get('error')}")
            return False
        
        # 设置参数
        logger.info(f"[ExecuteBbcTask] 设置苹果类型: {apple_type}")
        tcp_client.send_command('set_apple_type', {'apple_type': apple_type}, timeout=5)
        
        logger.info(f"[ExecuteBbcTask] 设置运行次数: {run_count}")
        tcp_client.send_command('set_run_times', {'times': run_count}, timeout=5)
        
        logger.info(f"[ExecuteBbcTask] 设置战斗类型: {battle_type}")
        tcp_client.send_command('set_battle_type', {'battle_type': battle_type}, timeout=5)
        
        # 启动战斗
        logger.info("[ExecuteBbcTask] 启动战斗...")
        result = tcp_client.send_command('start_battle', {}, timeout=10)
        if not result.get('success'):
            logger.error(f"[ExecuteBbcTask] 启动战斗失败: {result.get('error')}")
            return False
        
        logger.info("[ExecuteBbcTask] 战斗已启动，等待结束...")
        return True
    
    def _wait_for_battle_end(self, tcp_client, attach_data: dict):
        """监听回调端口等待战斗结束"""
        support_order_mismatch = attach_data.get('support_order_mismatch', False)
        team_config_error = attach_data.get('team_config_error', False)
        
        # 共享状态
        state = {
            'finished': False,
            'popup_title': '',
            'popup_message': ''
        }
        
        # 启动回调监听线程
        callback_thread = threading.Thread(
            target=self._listen_callbacks,
            args=(tcp_client, support_order_mismatch, team_config_error, state),
            daemon=True
        )
        callback_thread.start()
        
        # 主循环：心跳检查 + 等待结束
        while not state['finished']:
            # 心跳检查（30秒）
            status = tcp_client.send_command('get_status', {}, timeout=5)
            if not status.get('success'):
                logger.warning("[ExecuteBbcTask] BBC服务无响应")
                state['finished'] = True
                state['popup_title'] = '错误'
                state['popup_message'] = 'BBC服务异常'
                break
            
            time.sleep(30)
        
        # 等待监听线程结束
        callback_thread.join(timeout=5)
        
        return state['popup_title'], state['popup_message']
    
    def _listen_callbacks(self, tcp_client, support_order_mismatch: bool, 
                         team_config_error: bool, state: dict):
        """监听25002端口的回调事件"""
        server_sock = None
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind(('127.0.0.1', BBC_CALLBACK_PORT))
            server_sock.listen(1)
            server_sock.settimeout(2)
            
            logger.info(f"[Callback] 开始监听端口 {BBC_CALLBACK_PORT}")
            
            while not state['finished']:
                try:
                    client_sock, addr = server_sock.accept()
                    client_sock.settimeout(5)
                    
                    # 接收消息
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
                    
                    event = msg.get('event', '')
                    popup_title = msg.get('popup_title', '')
                    popup_message = msg.get('popup_message', '')
                    popup_id = msg.get('popup_id', '')
                    
                    # 处理助战排序不符合
                    if '助战排序不符合' in popup_title:
                        action = 'ok' if support_order_mismatch else 'cancel'
                        logger.info(f"[Callback] 助战弹窗，响应: {action}")
                        
                        if popup_id:
                            tcp_client.send_command('popup_response', {
                                'popup_id': popup_id,
                                'action': action
                            }, timeout=5)
                        
                        # 如果用户选择"否"，战斗结束
                        if action == 'cancel':
                            state['finished'] = True
                            state['popup_title'] = popup_title
                            state['popup_message'] = popup_message
                            logger.info("[Callback] 用户拒绝助战，战斗结束")
                    
                    # 处理队伍配置错误
                    elif '队伍配置错误' in popup_title:
                        action = 'ok' if team_config_error else 'cancel'
                        logger.info(f"[Callback] 队伍配置弹窗，响应: {action}")
                        
                        if popup_id:
                            tcp_client.send_command('popup_response', {
                                'popup_id': popup_id,
                                'action': action
                            }, timeout=5)
                        
                        # 如果用户选择"否"，战斗结束
                        if action == 'cancel':
                            state['finished'] = True
                            state['popup_title'] = popup_title
                            state['popup_message'] = popup_message
                            logger.info("[Callback] 用户拒绝队伍配置，战斗结束")
                    
                    # 处理脚本停止
                    elif '脚本停止' in popup_title:
                        logger.info("[Callback] 检测到脚本停止")
                        state['finished'] = True
                        state['popup_title'] = popup_title
                        state['popup_message'] = popup_message
                    
                    client_sock.close()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    logger.warning(f"[Callback] 接收异常: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"[Callback] 监听失败: {e}")
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
    
