/*
 * _ctypes.pyd 代理 DLL
 *
 * 编译 (MinGW-w64, Python 3.6):
 *   gcc -shared -o _ctypes.pyd proxy_ctypes.c -L<python_dir>/libs -lpython36 -O2
 *
 * 或者不链接 python36.lib，运行时动态获取（推荐，更通用）:
 *   gcc -shared -o _ctypes.pyd proxy_ctypes.c -O2
 *
 * 用法:
 *   1. 将原版 _ctypes.pyd 重命名为 _ctypes_orig.pyd
 *   2. 将编译出的 _ctypes.pyd 放到同目录
 *   3. 将 bbc_tcp_server.py 放到同目录
 */

#include <windows.h>
#include <stdio.h>

/* ---- Python C API 类型定义（避免依赖 Python.h） ---- */
typedef void* PyObject;
typedef PyObject* (*PyInit_Func)(void);
typedef PyObject* (*PyImport_ImportModule_Func)(const char*);
typedef int (*PyRun_SimpleString_Func)(const char*);
typedef void (*Py_DecRef_Func)(PyObject*);

/* ---- 全局状态 ---- */
static HMODULE hOriginal = NULL;          /* 原版 _ctypes_orig.pyd 句柄 */
static PyInit_Func original_PyInit = NULL; /* 原版 PyInit__ctypes 函数 */

/* ---- 要注入执行的 Python 代码 ---- */
/*
 * 修复 import lock 竞争问题：
 * 后台线程不在启动时立即 import bbc_tcp_server，
 * 而是先通过 gc.get_objects() 等待 BBchannelWindow 出现，
 * 确认主线程已完成所有关键 import 后再加载 bbc_tcp_server。
 */
static const char* inject_code =
    "import threading\n"
    "\n"
    "def _wait_and_start():\n"
    "    import time, gc\n"
    "    try:\n"
    "        for _ in range(200):\n"
    "            for obj in gc.get_objects():\n"
    "                if obj.__class__.__name__ == 'BBchannelWindow':\n"
    "                    import bbc_tcp_server\n"
    "                    bbc_tcp_server.start_tcp_server(obj, 25001)\n"
    "                    print('[BBC-TCP] BBchannelWindow found and connected')\n"
    "                    return\n"
    "            time.sleep(0.05)\n"
    "        print('[BBC-TCP] Warning: BBchannelWindow not found after 10s')\n"
    "    except Exception as e:\n"
    "        print(f'[BBC-TCP] Error: {e}')\n"
    "        import traceback\n"
    "        traceback.print_exc()\n"
    "\n"
    "threading.Thread(target=_wait_and_start, daemon=True).start()\n"
    "print('[BBC-TCP] Server thread started')\n";

/* ---- 注入逻辑 ---- */
static int injected = 0;

static void do_inject(void) {
    if (injected) return;
    injected = 1;

    /* 从已加载的 python36.dll 获取 API（BBchannel 进程中必然已加载） */
    HMODULE hPython = GetModuleHandleA("python36.dll");
    if (!hPython) {
        /* 尝试其他版本名 */
        hPython = GetModuleHandleA("python37.dll");
    }
    if (!hPython) {
        fprintf(stderr, "[proxy] Cannot find python DLL\n");
        return;
    }

    PyRun_SimpleString_Func pyRun =
        (PyRun_SimpleString_Func)GetProcAddress(hPython, "PyRun_SimpleString");
    if (!pyRun) {
        fprintf(stderr, "[proxy] Cannot find PyRun_SimpleString\n");
        return;
    }

    pyRun(inject_code);
}

/* ---- 导出: PyInit__ctypes ---- */
/*
 * Python import 机制调用此函数初始化 _ctypes 模块。
 * 我们先执行注入代码，再转发给原版。
 */
__declspec(dllexport)
PyObject* PyInit__ctypes(void) {
    /* 加载原版 */
    if (!hOriginal) {
        /* 获取自身所在目录 */
        char path[MAX_PATH];
        HMODULE hSelf;
        GetModuleHandleExA(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            (LPCSTR)PyInit__ctypes, &hSelf);
        GetModuleFileNameA(hSelf, path, MAX_PATH);

        /* 替换文件名为 _ctypes_orig.pyd */
        char* lastSlash = strrchr(path, '\\');
        if (lastSlash) {
            strcpy(lastSlash + 1, "_ctypes_orig.pyd");
        } else {
            strcpy(path, "_ctypes_orig.pyd");
        }

        hOriginal = LoadLibraryA(path);
        if (!hOriginal) {
            fprintf(stderr, "[proxy] Cannot load _ctypes_orig.pyd from: %s\n", path);
            return NULL;
        }

        original_PyInit = (PyInit_Func)GetProcAddress(hOriginal, "PyInit__ctypes");
        if (!original_PyInit) {
            fprintf(stderr, "[proxy] Cannot find PyInit__ctypes in original\n");
            return NULL;
        }
    }

    /* 先调用原版初始化（确保 ctypes 可用） */
    PyObject* module = original_PyInit();

    /* 然后注入 */
    do_inject();

    return module;
}

/* ---- DLL 入口 ---- */
BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD fdwReason, LPVOID lpReserved) {
    (void)hinstDLL;
    (void)lpReserved;
    switch (fdwReason) {
        case DLL_PROCESS_ATTACH:
            DisableThreadLibraryCalls(hinstDLL);
            break;
        case DLL_PROCESS_DETACH:
            if (hOriginal) {
                FreeLibrary(hOriginal);
                hOriginal = NULL;
            }
            break;
    }
    return TRUE;
}
