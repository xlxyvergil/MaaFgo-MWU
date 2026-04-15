import json
import os
import io
import time
import logging
import traceback
import tempfile
import shutil

import numpy as np
from PIL import Image

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 获取 Agent 根目录 (用于定位 resource 等文件夹)
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- 独立日志配置 ---
_nav_logger = logging.getLogger("GeneralNavigation")
if not _nav_logger.handlers:
    _nav_logger.setLevel(logging.DEBUG)
    _log_file = os.path.join(os.path.dirname(__file__), 'nav_debug.log')
    fh = logging.FileHandler(_log_file, mode='w', encoding='utf-8')
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    _nav_logger.addHandler(fh)
# --------------------


def _point_in_polygon(point, polygon):
    """射线法判断点是否在多边形内（纯 Python，无第三方依赖）"""
    x, y = point
    n = len(polygon)
    inside = False
    px, py = polygon[0]
    for i in range(1, n + 1):
        qx, qy = polygon[i % n]
        if min(py, qy) < y <= max(py, qy):
            if x < (qx - px) * (y - py) / (qy - py + 1e-10) + px:
                inside = not inside
        px, py = qx, qy
    return inside


@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action

        从地图坐标导航节点获取章节和关卡参数，执行地图相机定位和导航。
        使用 Pillow 替代 cv2 进行图像 IO / 缩放，
        使用 MaaFramework run_recognition (TemplateMatch) 替代 cv2.matchTemplate。
        """
        _nav_logger.info("=" * 50)
        _nav_logger.info("[Nav] general_navigation action started!")
        try:
            # 1. 从地图坐标导航节点获取参数
            _nav_logger.info("[Nav] Step 1: Getting node data...")
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                _nav_logger.error("[Nav] Error: node_data is None")
                return CustomAction.RunResult(success=False)

            attach_data = node_data.get("attach", {})
            chapter_code = attach_data.get("chapter", "")
            target_quest = attach_data.get("quests", "")
            _nav_logger.info(f"[Nav] Params: chapter={chapter_code}, quest={target_quest}")

            if not chapter_code or not target_quest:
                _nav_logger.error("[Nav] Error: Incomplete parameters")
                return CustomAction.RunResult(success=False)

            map_name = chapter_code.replace("c", "", 1) if chapter_code.startswith("c") else chapter_code
            _nav_logger.info(f"[Nav] Map name resolved: {map_name}")

            # 2. 加载地图坐标映射
            _nav_logger.info("[Nav] Step 2: Loading map_coordinates.json...")
            try:
                # 路径指向 agent/utils/map_coordinates.json
                map_file = os.path.join(os.path.dirname(__file__), "..", "utils", "map_coordinates.json")
                with open(map_file, 'r', encoding='utf-8') as f:
                    coordinates_data = json.load(f)
            except Exception as e:
                _nav_logger.error(f"[Nav] Error loading JSON: {e}")
                return CustomAction.RunResult(success=False)

            # 3. 获取目标关卡坐标
            _nav_logger.info(f"[Nav] Step 3: Searching for {map_name} -> {target_quest}")
            quest_list = coordinates_data.get("maps", {}).get(map_name, [])
            quest_coordinates = None
            for item in quest_list:
                if isinstance(item, list) and len(item) >= 2:
                    q_name, q_pos = item[0], item[1]
                    if q_name == target_quest:
                        quest_coordinates = q_pos
                        break

            if not quest_coordinates:
                _nav_logger.error("[Nav] Error: Coordinates not found in JSON")
                return CustomAction.RunResult(success=False)

            target_x, target_y = quest_coordinates
            _nav_logger.info(f"[Nav] Target coordinates found: ({target_x}, {target_y})")

            # 4. 加载大地图模板（Pillow 读取，转为 numpy array 供 run_recognition 使用）
            _nav_logger.info("[Nav] Step 4: Loading map template...")
            # 路径指向 agent/resource/common/image/地图坐标导航/{map_name}.png
            map_template_path = os.path.join(
                AGENT_ROOT, "resource", "common", "image",
                "地图坐标导航", f"{map_name}.png"
            )
            _nav_logger.info(f"[Nav] Template path resolved: {os.path.abspath(map_template_path)}")

            if not os.path.exists(map_template_path):
                _nav_logger.error(f"[Nav] Error: Template file missing at {map_template_path}")
                return CustomAction.RunResult(success=False)

            file_size = os.path.getsize(map_template_path)
            _nav_logger.info(f"[Nav] Template file size: {file_size} bytes ({file_size / 1024:.1f} KB)")

            with open(map_template_path, 'rb') as f:
                map_pil = Image.open(io.BytesIO(f.read())).convert("RGB")

            # MaaFramework 内部用 cv2 读取模板 PNG（BGR），run_recognition 传入的 numpy array
            # 也需为 BGR，才能与模板颜色通道一致。PIL 默认 RGB，需翻转。
            map_mat = np.array(map_pil)[:, :, ::-1].copy()  # (H, W, 3) uint8 BGR
            _nav_logger.info(f"[Nav] Template shape: {map_mat.shape}")
            # 保存调试用大地图（RGB，可直接查看）
            map_pil.save(os.path.join(os.path.dirname(__file__), 'debug_map_mat.png'))
            _nav_logger.info("[Nav] Saved debug_map_mat.png for inspection")

            # 5. 检测并隐藏 UI
            _nav_logger.info("[Nav] Step 5: Hiding UI via pipeline node...")
            controller = context.tasker.controller
            context.run_task("UI隐藏")
            time.sleep(1)

            # 获取截图（numpy array，可能是 RGB 或 RGBA）
            screen = controller.post_screencap().wait().get()
            if screen is None or screen.size == 0:
                _nav_logger.error("[Nav] Error: Screenshot failed")
                return CustomAction.RunResult(success=False)

            # 工具函数：处理一次截图 → 裁剪 → 缩放 → 保存临时文件
            def _prepare_screencap_template(raw_screen, iteration=0):
                """裁剪地图区域、缩放至 30%，保存为临时 PNG，返回路径"""
                # screencap 是 BGR(A)，必须先转为 RGB 再让 PIL 保存 PNG。
                # 原因：PIL 以 RGB 方式写入 PNG，cv2 读回时再按 BGR 解析，
                # 结果正好是原始 BGR 值——与 map_mat（BGR）颜色通道一致。
                if raw_screen.ndim == 3 and raw_screen.shape[2] == 4:
                    # BGRA → RGB（去 alpha，同时交换 R/B）
                    raw_rgb = raw_screen[:, :, [2, 1, 0]].astype(np.uint8)
                else:
                    # BGR → RGB
                    raw_rgb = raw_screen[:, :, ::-1].astype(np.uint8)
                pil_screen = Image.fromarray(raw_rgb)
                # 裁剪地图区域 [200:520, 200:1080] → PIL crop(left, top, right, bottom)
                region_pil = pil_screen.crop((200, 200, 1080, 520))  # 320×880
                # 缩放到 30%
                new_w = int(region_pil.width * 0.3)
                new_h = int(region_pil.height * 0.3)
                small_pil = region_pil.resize((new_w, new_h), Image.BICUBIC)
                # 保存到系统临时目录（绝对路径，MaaFramework template 支持）
                tmp = os.path.join(tempfile.gettempdir(), f"maa_nav_screencap_region_{iteration}.png")
                small_pil.save(tmp)
                
                # 保存调试用截图（仅前3次迭代）
                if iteration < 3:
                    debug_path = os.path.join(os.path.dirname(__file__), f'debug_screenshot_iter{iteration}.png')
                    pil_screen.save(debug_path)
                    _nav_logger.info(f"[Nav] Saved debug screenshot for iteration {iteration}")
                
                return tmp, region_pil

            # 第一次处理截图
            screencap_template_path, region_pil_debug = _prepare_screencap_template(screen, iteration=0)

            # 保存调试用裁剪图：region_pil_debug 已是 RGB（_prepare_screencap_template 内已转换），直接保存
            region_pil_debug.save(os.path.join(os.path.dirname(__file__), 'debug_map_region.png'))
            _nav_logger.info(f"[Nav] Saved debug_map_region.png (RGB), size={region_pil_debug.size}")
            # 同时保存缩放后用于匹配的小图
            dbg_small = os.path.join(os.path.dirname(__file__), 'debug_map_small.png')
            shutil.copy2(screencap_template_path, dbg_small)
            _nav_logger.info(f"[Nav] Saved debug_map_small.png (match template) from {screencap_template_path}")

            # 工具函数：在大地图中定位截图区域，返回 (current_x, current_y) 或 None
            def _locate_on_map(tmpl_path):
                # 计算模板文件的哈希值，用于调试
                import hashlib
                with open(tmpl_path, 'rb') as f:
                    tmpl_hash = hashlib.md5(f.read()).hexdigest()[:8]
                _nav_logger.info(f"[Nav] Matching template: {os.path.basename(tmpl_path)}, hash={tmpl_hash}")
                
                detail = context.run_recognition(
                    "LocateOnMap",
                    map_mat,
                    {
                        "LocateOnMap": {
                            "recognition": {
                                "type": "TemplateMatch",
                                "param": {
                                    "template": [tmpl_path],
                                    "threshold": 0.5,
                                    "method": 10001  # TM_SQDIFF_NORMED (inverted), 与 FGO-py 一致
                                }
                            }
                        }
                    }
                )
                
                if detail is None or detail.box is None:
                    _nav_logger.warning("[Nav] Template match failed (detail=None or box=None)")
                    return None
                    
                # detail.box = (x, y, w, h)，坐标在缩放后的大地图中
                loc_x = detail.box[0]
                loc_y = detail.box[1]
                score = detail.best_result.score if hasattr(detail, 'best_result') and detail.best_result else 'N/A'
                
                _nav_logger.info(f"[Nav] Match result - loc=({loc_x}, {loc_y}), box=({detail.box[2]}, {detail.box[3]}), score={score}")
                
                cx = int(loc_x / 0.3 + 440)
                cy = int(loc_y / 0.3 + 160)
                return cx, cy

            pos = _locate_on_map(screencap_template_path)
            if pos is None:
                _nav_logger.error("[Nav] Error: Map template match failed on initial screencap!")
                return CustomAction.RunResult(success=False)

            current_x, current_y = pos
            _nav_logger.info(f"[Nav] Initial camera position: ({current_x}, {current_y})")

            # 6. 导航循环
            # 地图可视区域多边形（与 FGO-py 一致）
            poly = [
                (230, 40), (230, 200), (40, 200), (40, 450),
                (150, 450), (220, 520), (630, 520), (630, 680),
                (980, 680), (980, 570), (1240, 570), (1240, 40)
            ]

            max_iterations = 10
            for iteration in range(max_iterations):
                _nav_logger.info(f"[Nav] --- Iteration {iteration + 1}/{max_iterations} ---")

                dx = target_x - current_x
                dy = target_y - current_y

                screen_target_x = 640 + dx
                screen_target_y = 360 + dy

                if _point_in_polygon((screen_target_x, screen_target_y), poly):
                    _nav_logger.info(
                        f"[Nav] Target is VISIBLE on screen at ({int(screen_target_x)}, {int(screen_target_y)})"
                    )
                    # 关闭地图说明弹窗（两次）
                    _nav_logger.info("[Nav] Closing map info popup...")
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)

                    # 点击目标关卡
                    controller.post_click(int(screen_target_x), int(screen_target_y)).wait()
                    _nav_logger.info("[Nav] Click executed. Waiting for game to return to quest selection...")
                    
                    # 等待游戏回到关卡选择界面（通常需要 2-3 秒）
                    time.sleep(3)
                    
                    _nav_logger.info("[Nav] Returning success=True")
                    return CustomAction.RunResult(success=True)

                _nav_logger.info(
                    f"[Nav] Target not visible (Screen pos: {int(screen_target_x)}, {int(screen_target_y)}). Swiping..."
                )

                distance = (dx ** 2 + dy ** 2) ** 0.5
                if distance == 0:
                    break

                scale = min(
                    590 / abs(dx) if dx != 0 else float('inf'),
                    310 / abs(dy) if dy != 0 else float('inf'),
                    0.5
                )
                slide_dx = dx * scale
                slide_dy = dy * scale

                start_x = 640 + slide_dx
                start_y = 360 + slide_dy
                end_x = 640 - slide_dx
                end_y = 360 - slide_dy

                _nav_logger.info(
                    f"[Nav] Swiping from ({int(start_x)}, {int(start_y)}) to ({int(end_x)}, {int(end_y)})"
                )
                controller.post_swipe(int(start_x), int(start_y), int(end_x), int(end_y), 1000).wait()
                _nav_logger.info("[Nav] Swipe executed. Waiting to stabilize...")
                time.sleep(1.5)

                # 重新截图并定位
                _nav_logger.info("[Nav] Re-capturing screenshot for re-positioning...")
                screen = controller.post_screencap().wait().get()
                if screen is None or screen.size == 0:
                    return CustomAction.RunResult(success=False)

                screencap_template_path, _ = _prepare_screencap_template(screen, iteration=iteration+1)
                pos = _locate_on_map(screencap_template_path)
                if pos is None:
                    _nav_logger.error("[Nav] Re-position match failed!")
                    return CustomAction.RunResult(success=False)

                current_x, current_y = pos
                _nav_logger.info(f"[Nav] New camera position: ({current_x}, {current_y})")

            _nav_logger.warning("[Nav] Navigation timed out after max iterations.")
            return CustomAction.RunResult(success=False)

        except Exception as e:
            error_trace = traceback.format_exc()
            _nav_logger.error(f"[Nav] CRITICAL EXCEPTION: {str(e)}\n{error_trace}")
            return CustomAction.RunResult(success=False)