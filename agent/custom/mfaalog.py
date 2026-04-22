import sys
import time

# 核心修改：MFA GUI 监听的是标准输出，且需要特定的前缀
# 开发者原话："focus或者字符串前面拼接info："

# 使用方式：在对应py脚本里import mfaalog，然后按照logger的方式填写即可
# mfaalog.error(f"[ExecuteBbcTask] 参数不完整: team={team_config}, count={run_count}, apple={apple_type}")

def _print_to_gui(prefix, msg):
    """
    基础输出函数
    :param prefix: 魔法前缀，如 'info:', 'error:', 'focus:'
    :param msg: 实际消息内容
    """
    # 获取当前时间（可选，有些GUI会自动加时间，你可以先试带时间的，如果重复了就去掉）
    # timestamp = time.strftime("%H:%M:%S", time.localtime())
    
    # 组合最终字符串
    # 格式可能需要是: "info:你的消息"
    final_msg = f"{prefix}{msg}"
    
    # 关键点1: 必须 flush，否则 Python 会缓存输出，导致 GUI 看起来卡顿或不显示
    print(final_msg, flush=True)

def info(msg):
    """普通日志"""
    # 对应开发者说的 "拼接 info:"
    _print_to_gui("info:", f"🟣 >>> {msg}")

def warning(msg):
    """警告日志"""
    # 尝试猜测 warning 的前缀，通常是 warn: 或 warning:，如果没有就用 info: [WARN]
    _print_to_gui("warn:", f"⚠️ >>> {msg}")

def error(msg):
    """错误日志"""
    # 尝试猜测 error 的前缀
    _print_to_gui("error:", f"🔴 >>> {msg}")

def debug(msg):
    """调试日志"""
    # debug 可能会被 GUI 过滤，如果显示不出来，可以改用 info: [DEBUG]
    _print_to_gui("debug:", msg)

def focus(task_id):
    """
    特殊指令：尝试让 GUI 聚焦/高亮某个任务
    开发者提到的 "focus" 可能指这个
    """
    _print_to_gui("focus:", task_id)

# ---------------------------------------------------------
# 必须保留的设置：防止中文乱码
# 如果 GUI 收到乱码，它可能直接丢弃整条日志，导致你看不到任何东西
# 注意：在嵌入式或GUI环境中，sys.stdout/sys.stderr可能是自定义包装器，不支持reconfigure()
if sys.version_info >= (3, 7):
    try:
        sys.stdout.reconfigure(encoding='utf-8')  # type: ignore
    except AttributeError:
        pass  # stdout不支持reconfigure()，跳过
    try:
        sys.stderr.reconfigure(encoding='utf-8')  # type: ignore
    except AttributeError:
        pass  # stderr不支持reconfigure()，跳过

# ---------------------------------------------------------
# 自测代码（直接运行这个文件测试）
if __name__ == "__main__":
    print("正在测试 MFA 日志协议...", flush=True)
    
    # 1. 开发者明确提到的格式
    info("这条日志应该能显示了！(基于 info: 前缀)")
    
    # 2. 测试其他等级
    time.sleep(0.5)
    warning("这是一条警告测试")
    error("这是一条错误测试")
    
    # 3. 测试 focus (假设任务ID是 task_1)
    time.sleep(0.5)
    focus("task_1")
    info("应该已经尝试聚焦任务了")