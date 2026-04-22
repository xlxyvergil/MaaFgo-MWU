"""
BBC 模拟器工具函数 - 连接参数解析与进程强制清理
"""
import os
import sys
import time
import psutil
import mfaalog


def get_connect_command_and_args(attach_data: dict):
    """
    根据 attach_data 中的 connect_mode 直接返回对应的命令名和参数字典。

    connect_mode 支持的值（直接可用，无需映射）：
    - 'connect_mumu' → 返回 mumu 模拟器相关参数
    - 'connect_ld'   → 返回 LD 模拟器相关参数
    - 'connect_adb'  → 返回 ADB 连接参数
    - 'auto'         → 自动连接已配置的设备

    Parameters:
        attach_data (dict): 节点 attach 配置，包含 connect_mode 及对应的连接参数。

    Returns:
        tuple: (connect_cmd: str, connect_args: dict)
    """
    mode = attach_data.get('connect_mode', 'auto')

    if mode == 'connect_mumu':
        connect_args = {
            'path':      attach_data.get('mumu_path', ''),
            'index':     int(attach_data.get('mumu_index', 0) or 0),
            'pkg':       attach_data.get('mumu_pkg', ''),
            'app_index': int(attach_data.get('mumu_app_index', 0) or 0),
        }
    elif mode == 'connect_ld':
        connect_args = {
            'path':  attach_data.get('ld_path', ''),
            'index': int(attach_data.get('ld_index', 0) or 0),
        }
    elif mode == 'connect_adb':
        connect_args = {
            'ip': attach_data.get('adb_ip', ''),
        }
    else:
        connect_args = {'mode': 'auto'}

    return mode, connect_args


def kill_bbc_processes():
    """
    强制清理所有 BBchannel.exe 进程，并记录每一步操作。

    策略（强制清理）：
    1. 用 psutil 扫描全部进程，找出命令行含 'BBchannel' 的目标进程。
    2. 对每个目标先调用 terminate()（SIGTERM），等待最多 3 秒。
    3. 若进程未退出，调用 kill()（SIGKILL / TerminateProcess）强制终止。
    4. 等待进程彻底消失后记录结果。
    5. 额外等待 1 秒，确保端口/文件锁释放。

    Raises:
        不抛出异常；所有错误均通过 mfaalog.error 记录。
    """
    mfaalog.info("[kill_bbc] 开始扫描 BBchannel 进程...")

    targets = []
    try:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                name = proc.info.get('name') or ''
                # 匹配进程名或命令行中含 BBchannel
                if 'BBchannel' in name or any('BBchannel' in arg for arg in cmdline):
                    targets.append(proc)
                    mfaalog.info(f"[kill_bbc] 发现目标进程: PID={proc.pid}, name={name}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception as e:
        mfaalog.error(f"[kill_bbc] 扫描进程失败: {e}")
        return

    if not targets:
        mfaalog.info("[kill_bbc] 未发现 BBchannel 进程，跳过清理")
        return

    mfaalog.info(f"[kill_bbc] 共发现 {len(targets)} 个目标进程，开始强制终止...")

    for proc in targets:
        pid = proc.pid
        try:
            # 第一步：尝试优雅终止
            if proc.is_running():
                mfaalog.info(f"[kill_bbc] terminate() PID={pid}")
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                    mfaalog.info(f"[kill_bbc] PID={pid} 已正常退出")
                    continue
                except psutil.TimeoutExpired:
                    mfaalog.warning(f"[kill_bbc] PID={pid} terminate 超时，改用 kill()")

            # 第二步：强制 kill
            if proc.is_running():
                proc.kill()
                try:
                    proc.wait(timeout=3)
                    mfaalog.info(f"[kill_bbc] PID={pid} 已被强制终止")
                except psutil.TimeoutExpired:
                    mfaalog.error(f"[kill_bbc] PID={pid} kill 后仍未退出，跳过")

        except psutil.NoSuchProcess:
            mfaalog.info(f"[kill_bbc] PID={pid} 已不存在")
        except psutil.AccessDenied:
            mfaalog.error(f"[kill_bbc] PID={pid} 无权限终止，尝试 taskkill...")
            try:
                import subprocess
                result = subprocess.run(
                    ['taskkill', '/F', '/PID', str(pid)],
                    capture_output=True, timeout=5, check=False
                )
                if result.returncode == 0:
                    mfaalog.info(f"[kill_bbc] taskkill PID={pid} 成功")
                else:
                    mfaalog.error(f"[kill_bbc] taskkill PID={pid} 失败: {result.stderr}")
            except Exception as e2:
                mfaalog.error(f"[kill_bbc] taskkill 异常: {e2}")
        except Exception as e:
            mfaalog.error(f"[kill_bbc] 终止 PID={pid} 时出错: {e}")

    # 额外等待，确保系统释放文件锁和端口
    time.sleep(1)
    mfaalog.info("[kill_bbc] 强制清理完成")