# bbcdll 技术文档

## 概述

`bbcdll/` 目录包含用于将 TCP 控制服务器注入 BBchannel 进程的文件。采用 **PYD DLL 代理 (DLL Proxy Hijacking)** 技术，在不修改 BBchannel 源码的前提下，使其启动时自动运行 `bbc_tcp_server.py`，从而让 MaaFgo 的 agent 层通过 TCP 协议远程控制 BBchannel 的战斗引擎。

## 文件说明

| 文件 | 说明 |
|---|---|
| `_ctypes.pyd` | **代理 DLL**（42KB），替代原版放入 BBchannel 目录 |
| `_ctypes_orig.pyd` | **原版 `_ctypes.pyd`**（125KB），从 BBchannel 中提取并重命名 |
| `bbc_tcp_server.py` | **TCP 服务器**，被注入代码 import 并启动 |
| `proxy_ctypes.c` | 代理 DLL 的 C 源码（参考实现） |
| `build.sh` | 编译脚本 |

## 技术原理

### 注入链路

BBchannel 是一个 PyInstaller 打包的 Python 3.6 应用。Python 运行时启动时会自动 `import _ctypes`（ctypes 模块的 C 扩展）。利用这一点，将原版 `_ctypes.pyd` 替换为代理 DLL：

```
BBchannel.exe 启动
  → Python 运行时初始化
  → import _ctypes
  → 加载代理 _ctypes.pyd（我们的 DLL）
  → PyInit__ctypes() 被调用
    ├─ LoadLibrary("_ctypes_orig.pyd")    // 加载原版
    ├─ 调用原版 PyInit__ctypes()           // 转发，保证 ctypes 功能正常
    ├─ PyRun_SimpleString(inject_code)    // 注入：启动后台线程
    └─ return module                      // ctypes 模块正常可用
```

### 注入代码逻辑

代理 DLL 内嵌的 Python 代码（通过 `PyRun_SimpleString` 执行）：

```python
import sys, threading, time, gc

def _wait_and_start():
    try:
        # 等待 BBchannel 主线程完成初始化（关键：避免 import lock 竞争）
        for _ in range(200):
            for obj in gc.get_objects():
                if obj.__class__.__name__ == 'BBchannelWindow':
                    import bbc_tcp_server
                    bbc_tcp_server.start_tcp_server(None, 25001)
                    bbc_tcp_server.update_bb_window(obj)
                    print('[BBC-TCP] BBchannelWindow found and connected')
                    return
            time.sleep(0.05)
        print('[BBC-TCP] Warning: BBchannelWindow not found')
    except Exception as e:
        print(f'[BBC-TCP] Error: {e}')
        import traceback
        traceback.print_exc()

t = threading.Thread(target=_wait_and_start, daemon=True)
t.start()
```

核心思路：通过 `gc.get_objects()` 遍历 Python 堆中所有对象，找到 `BBchannelWindow` 实例（BBchannel 的主窗口），说明主程序已完全初始化，此时再 import 并启动 TCP 服务器。

### 代理 DLL 结构

代理 DLL 使用 MinGW-w64 GCC 编译，结构如下：

| 组件 | 说明 |
|---|---|
| `DllMain` | DLL 入口，仅做 `DisableThreadLibraryCalls` 和资源清理 |
| `PyInit__ctypes` | **唯一导出函数**，Python import 机制的入口点 |
| `do_inject` | 注入逻辑，通过 `GetModuleHandle("python36.dll")` 获取 Python C API |
| `inject_code` | 嵌入的 Python 代码字符串 |

依赖关系：

```
代理 _ctypes.pyd
  ├─ KERNEL32.dll    （Windows 系统自带）
  └─ msvcrt.dll      （Windows 系统自带）

原版 _ctypes_orig.pyd
  ├─ KERNEL32.dll
  ├─ python36.dll    （BBchannel 自带）
  ├─ VCRUNTIME140.dll（需要 VC++ 运行时）
  ├─ ole32.dll       （Windows 系统自带）
  ├─ OLEAUT32.dll    （Windows 系统自带）
  └─ api-ms-win-crt-*（UCRT，Windows 10+ 自带）
```

代理 DLL 本身仅依赖系统自带 DLL，理论上不受本地环境影响。

## 已知问题：Import Lock 竞争

### 问题现象

在部分机器上，替换 PYD 并放入 `bbc_tcp_server.py` 后，BBchannel 启动报错："第80行 module 找不到"。移除 `bbc_tcp_server.py` 后恢复正常。即使 `bbc_tcp_server.py` 为空文件也会触发。

### 根因分析

Python 的 import 机制有**全局导入锁**（Python 3.6 尤为明显）。如果注入代码在后台线程中过早执行 `import bbc_tcp_server`，会与 BBchannel 主线程的模块导入产生锁竞争：

```
主线程                            后台线程
  │                                 │
  ├─ PyInit__ctypes 返回             │
  ├─ 继续执行 BBchannel 代码          ├─ import bbc_tcp_server
  ├─ 第80行 import xxx               │   读文件、编译字节码、写 __pycache__
  │   → 等待 import lock... 💥       │   持有 import lock...
```

即使是空文件，Python 也需要完整的 import 流程（查找→读取→编译→注册模块），足以触发竞争。

### 为什么部分机器正常

竞争条件是时序敏感的，是否触发取决于微秒级的线程调度差异：

- **CPU 速度/核心数**：影响线程调度时机
- **磁盘速度**：SSD vs HDD，文件读取编译的快慢
- **系统负载**：影响 OS 线程调度器的行为

别人机器上竞争同样存在，只是恰好没有撞上临界时序。

### 解决方案

将 `import bbc_tcp_server` 推迟到 BBchannel 主线程完全初始化之后。具体方法是先通过 `gc.get_objects()` 等待 `BBchannelWindow` 对象出现，再执行 import：

```python
# 错误：过早 import，可能触发 lock 竞争
def _wait_and_start():
    import bbc_tcp_server                    # ← 立即 import，危险
    bbc_tcp_server.start_tcp_server(...)
    for _ in range(200):
        for obj in gc.get_objects():
            if obj.__class__.__name__ == 'BBchannelWindow':
                bbc_tcp_server.update_bb_window(obj)
                return
        time.sleep(0.05)

# 正确：等待主程序初始化完成后再 import
def _wait_and_start():
    for _ in range(200):
        for obj in gc.get_objects():
            if obj.__class__.__name__ == 'BBchannelWindow':
                import bbc_tcp_server        # ← 主程序就绪后才 import，安全
                bbc_tcp_server.start_tcp_server(None, 25001)
                bbc_tcp_server.update_bb_window(obj)
                return
        time.sleep(0.05)
```

## 构建方法

### 前置条件

- MinGW-w64 GCC（x86_64）
  - 通过 MSYS2 安装：`pacman -S mingw-w64-x86_64-gcc`
  - 或下载独立版：https://www.mingw-w64.org/

### 编译

```bash
cd bbcdll/
gcc -shared -o _ctypes.pyd proxy_ctypes.c -O2 -Wall -lkernel32
```

### 部署

1. 进入 BBchannel 目录：`BBchannel/dist/BBchannel64/`
2. 备份原版：`mv _ctypes.pyd _ctypes_orig.pyd`
3. 复制代理 DLL：`cp <构建输出>/_ctypes.pyd ./`
4. 复制 TCP 服务器：`cp bbc_tcp_server.py ./`

安装脚本 (`tools/install*.py`) 中的 `install_bbcdll()` 会自动完成上述部署。

### 验证

部署后启动 BBchannel，检查控制台输出：

```
[BBC-TCP] Server thread started
[BBC-TCP] Server started early
[TCP-Server] Started on 127.0.0.1:25001
[BBC-TCP] BBchannelWindow found and connected
```

如果看到以上输出，说明注入成功，TCP 服务器已在 25001 端口监听。

## 替代方案

如果 PYD 代理方案不适用，可以考虑以下替代：

| 方案 | 原理 | 优缺点 |
|---|---|---|
| `sitecustomize.py` | Python 启动时自动 import | 纯 Python，零编译；PyInstaller 可能禁用了 site 模块 |
| 劫持其他 PYD/PY 模块 | 替换 BBchannel 必定 import 的其他模块 | 灵活但需确认目标模块 |
| Windows DLL 注入 | 从外部进程注入 DLL 到 BBchannel | 不修改 BBchannel 文件；会被杀软拦截 |
| 修改 PyInstaller 包 | 解包→修改启动脚本→重新打包 | 一劳永逸；每次更新都要重做 |

### sitecustomize.py 可用性测试

在 BBchannel 目录下创建：

```python
# sitecustomize.py
import os, datetime
with open(os.path.join(os.path.dirname(__file__), "site_test.txt"), "w") as f:
    f.write(f"executed at {datetime.datetime.now()}\n")
```

启动 BBchannel 后检查是否生成 `site_test.txt`。如果生成，说明 `sitecustomize.py` 可用，可以替代 PYD 代理方案。

## 通信协议

注入成功后，TCP 服务器在 `127.0.0.1:25001`（命令端口）和 `127.0.0.1:25002`（回调端口）监听。

### 消息格式

```
[4字节长度 (big-endian)] [JSON 数据 (UTF-8)]
```

### 命令示例

```json
{"cmd": "connect_mumu", "args": {"path": "D:\\MuMu", "index": 0}}
{"cmd": "load_config", "args": {"filename": "battle.json"}}
{"cmd": "start_battle", "args": {}}
{"cmd": "get_status", "args": {}}
```

### 支持的命令

| 分类 | 命令 |
|---|---|
| 连接管理 | `connect_mumu`, `connect_ld`, `connect_adb`, `disconnect`, `get_connection` |
| 配置管理 | `load_config`, `save_config`, `get_config` |
| 战斗设置 | `set_apple_type`, `set_run_times`, `set_battle_type`, `get_settings` |
| 战斗控制 | `start_battle`, `stop_battle`, `pause_battle`, `resume_battle` |
| 状态查询 | `get_status`, `get_ui_status`, `get_popups`, `popup_response`, `wait_for_popup` |
