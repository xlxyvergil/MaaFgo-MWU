import json
import os
import time
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 3-7 楼层电梯按钮坐标 (从 FGO-py fgoReishift.py Mictlan.elevator)
ELEVATOR_BUTTONS = [(1215, 560 - 54 * i) for i in range(9)]

# 楼层索引映射表 (用于双层点击策略)
FLOOR_REFERENCE = [1, 2, 3, 4, 5, 6, 7, 8, 7]

# 关卡名称到楼层的映射 (从 FGO-py fgoReishift.py Mictlan 定义提取)
QUEST_TO_FLOOR = {
    "奇科莫斯托克": 0,
    "贤者的隐居之所": 0,
    "玉米地": 1,
    "特拉特拉乌基": 1,
    "大平原": 2,
    "奇琴伊察": 2,
    "卡恩的废墟": 2,
    "坠机点": 3,
    "伊斯塔乌基": 3,
    "墨西哥城": 4,
    "烤玉米地": 4,
    "索索亚乌基": 5,
    "蒂卡尔遗迹": 6,
    "梅兹蒂特兰": 7,
    "亚亚乌基": 7,
    "卡拉克穆尔": 8,
}

@AgentServer.custom_action("mictlan_navigation")
class MictlanNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """
        纳维·米克特兰 (3-7) 专用导航 Action
        通过 attach 传入 chapter 和 quest 参数
        """
        mfaalog.info("=" * 50)
        mfaalog.info("[MictlanNav] Navigation action started!")
        
        try:
            # 1. 从节点数据获取参数
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                mfaalog.error("[MictlanNav] No node data found")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get("attach", {})
            chapter_cn = attach_data.get("chapter", "")
            target_quest = attach_data.get("quests", "")
            
            if not chapter_cn or not target_quest:
                mfaalog.error(f"[MictlanNav] Missing parameters: chapter={chapter_cn}, quest={target_quest}")
                return CustomAction.RunResult(success=False)
            
            mfaalog.info(f"[MictlanNav] Chapter: {chapter_cn}, Quest: {target_quest}")
            
            # 2. 验证章节名称
            if chapter_cn != "纳维米克特兰":
                mfaalog.error(f"[MictlanNav] Invalid chapter: {chapter_cn}. This action is for 纳维米克特兰 only.")
                return CustomAction.RunResult(success=False)
            
            # map_coordinates.json 中使用相同的键名
            json_chapter_key = chapter_cn
            
            # 3. 加载地图坐标 JSON
            AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            map_file = os.path.join(AGENT_DIR, "utils", "map_coordinates.json")
            
            if not os.path.exists(map_file):
                mfaalog.error(f"[MictlanNav] Map file NOT found: {map_file}")
                return CustomAction.RunResult(success=False)
            
            with open(map_file, 'r', encoding='utf-8') as f:
                coordinates_data = json.load(f)
            
            quest_list = coordinates_data.get("maps", {}).get(json_chapter_key, [])
            mfaalog.info(f"[MictlanNav] Found {len(quest_list)} quests in '{json_chapter_key}'")
            
            # 4. 查找目标关卡坐标
            quest_coordinates = None
            for item in quest_list:
                if isinstance(item, list) and len(item) >= 2:
                    q_name, q_pos = item[0], item[1]
                    if q_name == target_quest:
                        quest_coordinates = q_pos
                        mfaalog.info(f"[MictlanNav] Found coordinates for '{target_quest}': {q_pos}")
                        break
            
            if not quest_coordinates:
                mfaalog.error(f"[MictlanNav] Coordinates NOT found for quest: {target_quest}")
                return CustomAction.RunResult(success=False)
            
            # 5. 通过映射表获取楼层
            floor = QUEST_TO_FLOOR.get(target_quest)
            if floor is None:
                mfaalog.error(f"[MictlanNav] Floor mapping NOT found for quest: {target_quest}")
                return CustomAction.RunResult(success=False)
            
            mfaalog.info(f"[MictlanNav] Target: Floor {floor}, Click at {quest_coordinates}")
            
            # 6. 执行导航操作
            controller = context.tasker.controller
            
            # 等待主界面
            mfaalog.info("[MictlanNav] Waiting for main interface...")
            context.run_task("UI隐藏")
            time.sleep(1.6)
            
            # 获取电梯按钮坐标
            ref_floor_idx = FLOOR_REFERENCE[floor]
            ref_button = ELEVATOR_BUTTONS[ref_floor_idx]
            target_button = ELEVATOR_BUTTONS[floor]
            
            mfaalog.info(f"[MictlanNav] Reference floor button: {ref_button}, Target floor button: {target_button}")
            
            # 7. 双层点击策略
            # 第一步：点击参考楼层按钮（用于刷新/定位）
            mfaalog.info(f"[MictlanNav] Step 1: Clicking reference floor button at {ref_button}")
            controller.post_click(ref_button[0], ref_button[1]).wait()
            time.sleep(2.0)
            
            # 第二步：点击目标楼层按钮
            mfaalog.info(f"[MictlanNav] Step 2: Clicking target floor button at {target_button}")
            controller.post_click(target_button[0], target_button[1]).wait()
            time.sleep(2.0)
            
            # 第三步：点击关卡坐标
            mfaalog.info(f"[MictlanNav] Step 3: Clicking quest at {quest_coordinates}")
            controller.post_click(quest_coordinates[0], quest_coordinates[1]).wait()
            time.sleep(1.0)
            
            mfaalog.info("[MictlanNav] Navigation completed successfully!")
            return CustomAction.RunResult(success=True)
                
        except Exception as e:
            mfaalog.error(f"[MictlanNav] CRITICAL ERROR: {str(e)}", exc_info=True)
            return CustomAction.RunResult(success=False)
