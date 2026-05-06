"""
通用导航 Action - 基于图像匹配的大地图坐标导航

功能说明：
- 通过 OpenCV 模板匹配定位当前在大地图中的位置
- 计算目标关卡坐标与当前位置的偏移量
- 自动滑动地图直到目标可见，然后点击选择
- 支持所有章节的大地图导航（通过 CHAPTER_MAP 映射）

工作原理：
1. 加载章节对应的大地图模板图片
2. 截取游戏屏幕中的小地图区域并缩放
3. 使用 TM_SQDIFF_NORMED 算法进行模板匹配，计算当前位置
4. 判断目标是否在可视区域内（多边形检测）
5. 如果不可见，计算滑动距离并执行滑动手势
6. 重复步骤2-5直到目标可见或达到最大迭代次数
"""
import json
import os
import time
import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 中英文章节名映射表：将中文章节名映射到资源文件中的英文文件名
CHAPTER_MAP = {
    "冬木": "Fuyuki",
    "奥尔良": "Orleans",
    "七丘之城": "Septem",
    "俄刻阿诺斯": "Okeanos",
    "伦敦": "London",
    "合众为一": "Unum",
    "卡美洛": "Camelot",
    "巴比伦尼亚": "Babylonia",
    "新宿": "Shinjuku",
    "雅戈泰": "Agartha",
    "下总国": "Shimousa",
    "塞勒姆": "Salem",
    "阿纳斯塔西娅": "Anastasia",
    "诸神黄昏": "Gotterdammerung",
    "SIN": "SIN",
    "由伽刹多罗": "Yugakshetra",
    "亚特兰蒂斯": "Atlantis",
    "奥林波斯": "Olympus",
    "平安京": "Heiankyo",
    "阿瓦隆勒菲": "AvalonleFae",
    "Traum": "Traum",
    "平面之月": "PaperMoon",
    "原型肇始": "Archetype",
    "伊底": "Gehenna",
    "梅塔特洛尼俄斯": "Metatronius",
}

@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action - 基于图像匹配的大地图坐标导航
        
        执行流程：
        1. 从节点数据提取章节和关卡参数
        2. 加载地图坐标 JSON 配置文件
        3. 查找目标关卡的绝对坐标
        4. 加载大地图模板图片
        5. 截图并进行图像匹配，计算当前位置
        6. 循环滑动地图直到目标可见，然后点击选择
        """
        mfaalog.info("="*50)
        mfaalog.info("[导航] 通用导航 Action 启动（CV2 图像匹配模式）")
        try:
            # 步骤1: 从地图坐标导航节点获取参数
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                mfaalog.error("[导航] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get("attach", {})
            chapter_cn = attach_data.get("chapter", "")  # 中文章节名
            target_quest = attach_data.get("quests", "")  # 目标关卡名
            
            if not chapter_cn or not target_quest:
                mfaalog.error(f"[导航] 参数缺失: chapter={chapter_cn}, quest={target_quest}")
                return CustomAction.RunResult(success=False)
            
            # 步骤2: 章节名映射（中文 -> 英文文件名，用于加载大地图图片）
            map_image_name = CHAPTER_MAP.get(chapter_cn, chapter_cn)
            mfaalog.info(f"[导航] chapter: {chapter_cn}, image_name: {map_image_name}, quest: {target_quest}")

            # 步骤3: 加载地图坐标映射 JSON
            # 脚本在 agent/custom/，往上两级是 agent/
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            # JSON 在 agent/utils/ 下
            map_file = os.path.join(AGENT_DIR, "utils", "map_coordinates.json")
            
            mfaalog.info(f"[导航] 查找地图文件: {map_file}")
            if not os.path.exists(map_file):
                mfaalog.error(f"[导航] 地图文件不存在: {map_file}")
                return CustomAction.RunResult(success=False)

            with open(map_file, 'r', encoding='utf-8') as f:
                coordinates_data = json.load(f)
            
            # 获取当前章节的关卡列表
            quest_list = coordinates_data.get("maps", {}).get(chapter_cn, [])
            mfaalog.info(f"[导航] 在 map '{chapter_cn}' 中找到 {len(quest_list)} 个 quest")
            
            # 遍历查找目标关卡的坐标
            quest_coordinates = None
            for item in quest_list:
                if isinstance(item, list) and len(item) >= 2:
                    q_name, q_pos = item[0], item[1]
                    if q_name == target_quest:
                        quest_coordinates = q_pos
                        mfaalog.info(f"[导航] 找到 quest '{target_quest}' 的坐标: {q_pos}")
                        break
                        
            if not quest_coordinates:
                mfaalog.error(f"[导航] 未找到 quest '{target_quest}' 的坐标")
                return CustomAction.RunResult(success=False)
            
            target_x, target_y = quest_coordinates  # 目标关卡的绝对坐标

            # 步骤4: 加载大地图模板图片
            mfaalog.info("[导航] 步骤4: 加载大地图模板...")
            ROOT_DIR = os.path.dirname(AGENT_DIR)
            map_template_path = os.path.join(ROOT_DIR, "resource", "base", "image", "map", f"{map_image_name}.png")
            mfaalog.info(f"[导航] 模板路径: {map_template_path}")
            
            map_template = cv2.imread(map_template_path)
            
            mfaalog.info(f"[导航] 模板尺寸: {map_template.shape if map_template is not None else 'None'}")
            if map_template is None:
                mfaalog.error(f"[导航] 错误: 无法加载模板图片 {map_template_path}")
                return CustomAction.RunResult(success=False)

            # 步骤5: 截图与预处理
            controller = context.tasker.controller
            context.run_task("UI隐藏")  # 隐藏UI元素，避免干扰图像匹配
            time.sleep(1)
            
            # 使用 MaaFramework 标准截图接口（返回 BGR numpy array）
            screen = controller.post_screencap().wait().get()
            
            if screen is None:
                mfaalog.error("[导航] 截图失败")
                return CustomAction.RunResult(success=False)

            # 步骤6: 图像匹配计算当前坐标
            # 截取屏幕中的小地图区域（x: 200-1080, y: 200-520）
            map_region = screen[200:520, 200:1080]
            # 缩放到 0.3 倍，加速匹配计算
            resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
            
            # 使用 TM_SQDIFF_NORMED 算法进行模板匹配（值越小匹配度越高）
            result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
            min_val, _, min_loc, _ = cv2.minMaxLoc(result)
            
            # 匹配阈值检查：min_val > 0.5 表示匹配失败
            if min_val > 0.5:
                mfaalog.error(f"[导航] 初始匹配失败! min_val={min_val}")
                return CustomAction.RunResult(success=False)

            # 计算当前在大地图中的绝对坐标
            # min_loc 是缩放后的相对位置，需要还原并加上偏移量
            current_x = int(min_loc[0] / 0.3 + 440)
            current_y = int(min_loc[1] / 0.3 + 160)
            mfaalog.info(f"[导航] initial position: ({current_x}, {current_y})")

            # 步骤7: 导航循环（最多迭代10次）
            # 定义可视区域多边形（12个顶点）
            poly = np.array([
                [230, 40], [230, 200], [40, 200], [40, 450],
                [150, 450], [220, 520], [630, 520], [630, 680],
                [980, 680], [980, 570], [1240, 570], [1240, 40]
            ])
            
            for iteration in range(10):
                # 计算目标与当前位置的偏移量
                dx = target_x - current_x
                dy = target_y - current_y
                # 计算目标在屏幕上的坐标（屏幕中心 640, 360 + 偏移量）
                screen_target_x = 640 + dx
                screen_target_y = 360 + dy
                
                # 检查目标是否在可视区域内（多边形检测）
                if cv2.pointPolygonTest(poly, (float(screen_target_x), float(screen_target_y)), False) >= 0:
                    mfaalog.info("[导航] 目标可见，点击选择...")
                    # 先点击两次确认按钮（坐标 1231, 687）
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)
                    controller.post_click(1231, 687).wait()
                    time.sleep(0.3)
                    # 点击目标关卡
                    controller.post_click(int(screen_target_x), int(screen_target_y)).wait()
                    return CustomAction.RunResult(success=True)
                
                # 滑动逻辑：计算滑动距离
                distance = (dx**2 + dy**2)**0.5
                if distance == 0: break
                
                # 计算缩放比例，确保滑动距离不超过边界
                # X方向最大滑动 590 像素，Y方向最大滑动 310 像素，整体不超过 0.5 倍
                scale = min(590/abs(dx) if dx != 0 else float('inf'), 310/abs(dy) if dy != 0 else float('inf'), 0.5)
                slide_dx, slide_dy = dx * scale, dy * scale
                
                # 执行滑动手势：从 (640+slide_dx, 360+slide_dy) 滑到 (640-slide_dx, 360-slide_dy)
                # 持续时间 1000ms
                controller.post_swipe(int(640 + slide_dx), int(360 + slide_dy), int(640 - slide_dx), int(360 - slide_dy), 1000).wait()
                time.sleep(1.5)  # 等待滑动动画完成
                
                # 重新定位：再次截图并匹配
                screen = controller.post_screencap().wait().get()
                if screen is None:
                    mfaalog.error("[导航] 重新截图失败")
                    return CustomAction.RunResult(success=False)
                
                map_region = screen[200:520, 200:1080]
                resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
                result = cv2.matchTemplate(map_template, resized_map_region, cv2.TM_SQDIFF_NORMED)
                min_val, _, min_loc, _ = cv2.minMaxLoc(result)
                
                if min_val > 0.5:
                    mfaalog.error(f"[导航] 重新匹配失败! min_val={min_val}")
                    return CustomAction.RunResult(success=False)
                
                # 更新当前位置
                current_x = int(min_loc[0] / 0.3 + 440)
                current_y = int(min_loc[1] / 0.3 + 160)
                mfaalog.info(f"[导航] new position: ({current_x}, {current_y})")

            mfaalog.error("[导航] 达到最大迭代次数，导航失败")
            return CustomAction.RunResult(success=False)
                
        except Exception as e:
            mfaalog.error(f"[导航] 严重错误: {str(e)}")
            return CustomAction.RunResult(success=False)
