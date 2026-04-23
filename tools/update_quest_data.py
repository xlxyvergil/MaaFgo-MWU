"""
副本敌人数据 & 周常任务数据更新工具

从 Atlas Academy API 下载国服 Free 副本的敌人信息和周常任务历史数据。
保存到 assets/resource/Chaldea/ 目录，供周常任务求解器离线使用。

用法:
    python tools/update_quest_data.py
    python tools/update_quest_data.py --region JP
    python tools/update_quest_data.py --missions-only   # 仅更新周常任务
    python tools/update_quest_data.py --quests-only     # 仅更新副本数据
"""

import json
import os
import ssl
import sys
import time
import argparse
import urllib.request
from typing import Optional

# 强制 stdout UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ATLAS_API = "https://api.atlasacademy.io"
CHALDEA_DATA_HOST = "https://data.chaldea.center"

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_TOOLS_DIR, "..", "assets", "resource", "Chaldea")

# 主线 War ID 范围（国服）
# War 100-399: 主线章节 (特异点/Lostbelt)
# War 1003: 迦勒底之门（修炼场等日常本）
MAIN_STORY_WAR_RANGE = range(100, 500)
CHALDEA_GATE_WAR_ID = 1003
ORDEAL_CALL_WAR_ID = 311  # 异闻带的试炼

# Trait ID -> classId 映射（Chaldea 数据中的 traits 包含职阶信息）
_TRAIT_CLASS_MAP = {
    100: 1,   # Saber
    101: 3,   # Lancer
    102: 2,   # Archer
    103: 4,   # Rider
    104: 5,   # Caster
    105: 6,   # Assassin
    106: 7,   # Berserker
    107: 9,   # Ruler
    108: 8,   # Shielder
    109: 10,  # Avenger
    110: 11,  # AlterEgo
    111: 12,  # MoonCancer
    112: 13,  # Foreigner
    113: 14,  # Pretender
}


def fetch_json(url: str, timeout: int = 60, retries: int = 3) -> Optional[dict | list]:
    """下载 JSON，带重试"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})

    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt < retries - 1:
                print(f"    重试 ({attempt + 1}/{retries}): {e}")
                time.sleep(2)
            else:
                print(f"    失败: {e}")
                return None


def _extract_class_id_from_traits(traits: list) -> int:
    """从 traits 中提取 classId"""
    for t in traits:
        tid = t.get("id", t) if isinstance(t, dict) else t
        if tid in _TRAIT_CLASS_MAP:
            return _TRAIT_CLASS_MAP[tid]
    return 0


def update_quest_enemies_from_chaldea(region: str = "CN"):
    """从 Chaldea 数据服务器下载 Free 副本的敌人数据"""
    print(f"\n[更新副本敌人数据 - Chaldea数据源] region={region}")

    # 1. 获取版本信息
    version_data = fetch_json(f"{CHALDEA_DATA_HOST}/version.json")
    if version_data is None:
        print("  [FAIL] 无法获取 Chaldea 版本信息")
        return

    files = version_data.get("files", {})
    quest_files = [v for k, v in files.items() if v.get("key") == "questPhases"]
    if not quest_files:
        print("  [FAIL] 未找到 questPhases 文件")
        return

    print(f"  发现 {len(quest_files)} 个 questPhases 文件")

    # 2. 下载所有 questPhases 文件
    all_quests = {}
    for qf in quest_files:
        filename = qf.get("filename")
        url = f"{CHALDEA_DATA_HOST}/{filename}"
        print(f"  下载 {filename}...", end=" ")
        data = fetch_json(url)
        if data is None:
            print("失败")
            continue
        print(f"{len(data)} 个 quest")

        for q in data:
            quest_id = q.get("id", 0)
            phase = q.get("phase", 1)
            key = quest_id * 100 + phase
            all_quests[key] = q

    print(f"  总计: {len(all_quests)} 个 quest phase")

    # 3. 筛选 Free 副本并提取敌人数据
    free_quests = {}
    for q in all_quests.values():
        if q.get("afterClear") != "repeatLast":
            continue
        if not q.get("stages"):
            continue

        quest_id = q["id"]
        enemies = []
        for stage in q.get("stages", []):
            for enemy in stage.get("enemies", []):
                traits = [t.get("id", t) if isinstance(t, dict) else t for t in enemy.get("traits", [])]
                enemy_info = {
                    "svtId": enemy.get("svt", {}).get("id", 0),
                    "classId": _extract_class_id_from_traits(traits),
                    "traits": traits,
                    "deck": "enemy",
                    "isServant": 1000 in traits,
                }
                enemies.append(enemy_info)

        # 获取副本名称（国服需要中文名）
        quest_name = q.get("name", str(quest_id))

        quest_entry = {
            "id": quest_id,
            "name": quest_name,
            "consume": q.get("consume", 0),
            "warId": q.get("warId", 0),
            "individuality": [
                t.get("id", t) if isinstance(t, dict) else t
                for t in q.get("individuality", [])
            ],
            "enemies": enemies,
        }
        free_quests[str(quest_id)] = quest_entry

    # 保存
    out_path = os.path.join(DATA_DIR, f"quest_enemies_{region}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(free_quests, f, ensure_ascii=False, indent=2)
    print(f"\n  [OK] 副本敌人数据: {len(free_quests)} 个 Free 副本 -> {out_path}")


def update_quest_enemies(region: str = "CN"):
    """下载所有主线 Free 副本的敌人数据（Atlas Academy API）"""
    print(f"\n[更新副本敌人数据 - Atlas API] region={region}")

    # 1. 获取主线 War 列表
    all_quests = {}
    war_ids = list(MAIN_STORY_WAR_RANGE) + [CHALDEA_GATE_WAR_ID]

    for war_id in war_ids:
        print(f"  获取 War {war_id}...", end=" ")
        war_data = fetch_json(f"{ATLAS_API}/nice/{region}/war/{war_id}")
        if war_data is None:
            print("跳过")
            continue

        free_quests = []
        # 国服 War 数据: quests 在 spots 中
        quests_source = war_data.get("quests", [])
        if not quests_source:
            for spot in war_data.get("spots", []):
                quests_source.extend(spot.get("quests", []))

        for quest in quests_source:
            # 筛选 Free 副本: afterClear == "repeatLast" 且有多个 phase
            after_clear = quest.get("afterClear", "")
            if after_clear == "repeatLast" and quest.get("phases"):
                free_quests.append(quest)

        if not free_quests:
            print(f"无 Free 副本")
            continue

        print(f"{len(free_quests)} 个 Free 副本")

        # 2. 获取每个 Free 副本的敌人数据
        for quest in free_quests:
            quest_id = quest["id"]
            phase = quest["phases"][-1]  # 取最后一个 phase
            quest_name = quest.get("name", str(quest_id))

            print(f"    [{quest_id}] {quest_name} phase={phase}...", end=" ")

            phase_data = fetch_json(f"{ATLAS_API}/nice/{region}/quest/{quest_id}/{phase}")
            if phase_data is None:
                print("失败")
                continue

            # 提取敌人信息
            enemies = []
            for stage in phase_data.get("stages", []):
                for enemy in stage.get("enemies", []):
                    svt = enemy.get("svt", {})
                    enemy_info = {
                        "svtId": svt.get("id", 0),
                        "classId": svt.get("className", 0),
                        "traits": [t.get("id", t) if isinstance(t, dict) else t for t in enemy.get("traits", [])],
                        "deck": enemy.get("deck", "enemy"),
                        "isServant": any(
                            (t.get("id", t) if isinstance(t, dict) else t) == 1000
                            for t in enemy.get("traits", [])
                        ),
                    }
                    # classId 可能是字符串（如 "saber"），转成数字
                    class_name = svt.get("className", "")
                    class_id = svt.get("classId", 0)
                    if isinstance(class_id, int) and class_id > 0:
                        enemy_info["classId"] = class_id
                    elif isinstance(class_name, str):
                        enemy_info["classId"] = _class_name_to_id(class_name)

                    enemies.append(enemy_info)

            quest_entry = {
                "id": quest_id,
                "name": quest_name,
                "consume": phase_data.get("consume", quest.get("consume", 0)),
                "warId": war_id,
                "individuality": [
                    t.get("id", t) if isinstance(t, dict) else t
                    for t in phase_data.get("individuality", [])
                ],
                "enemies": enemies,
            }
            all_quests[str(quest_id)] = quest_entry
            print(f"{len(enemies)} 个敌人")

            time.sleep(0.3)  # 控制请求频率

    # 保存
    out_path = os.path.join(DATA_DIR, f"quest_enemies_{region}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_quests, f, ensure_ascii=False, indent=2)
    print(f"\n  [OK] 副本敌人数据: {len(all_quests)} 个副本 -> {out_path}")


def update_master_missions(region: str = "CN"):
    """下载周常任务数据"""
    print(f"\n[更新周常任务数据] region={region}")

    data = fetch_json(f"{ATLAS_API}/export/{region}/nice_master_mission.json")
    if data is None:
        print("  [FAIL] 下载失败")
        return

    # 转换为简化格式
    schedule = []
    for mm in data:
        started_at = mm.get("startedAt", 0)
        ended_at = mm.get("endedAt", 0)

        # 时间戳转日期字符串
        try:
            started_str = time.strftime("%Y-%m-%d", time.gmtime(started_at)) if started_at else ""
            ended_str = time.strftime("%Y-%m-%d", time.gmtime(ended_at)) if ended_at else ""
        except (OSError, ValueError):
            continue

        missions = []
        for em in mm.get("missions", []):
            parsed = _parse_event_mission(em)
            if parsed:
                missions.append(parsed)

        if missions:
            schedule.append({
                "id": mm.get("id", 0),
                "startedAt": started_str,
                "endedAt": ended_str,
                "missions": missions,
            })

    # 按开始时间排序
    schedule.sort(key=lambda x: x["startedAt"])

    out_path = os.path.join(DATA_DIR, f"master_missions_{region}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)
    print(f"  [OK] 周常任务: {len(schedule)} 周 -> {out_path}")


def _parse_event_mission(em: dict) -> Optional[dict]:
    """
    解析单条 EventMission 为简化格式。

    参考 Chaldea CustomMission.fromEventMission()
    """
    for cond in em.get("conds", []):
        if cond.get("missionProgressType") != "clear":
            continue
        if cond.get("condType") != "missionConditionDetail":
            continue

        details = cond.get("details", [])
        if not details:
            continue

        conds = []
        for detail in details:
            cond_type = _map_cond_type(detail.get("missionCondType", 0))
            if cond_type is None:
                continue

            target_ids = detail.get("targetIds", [])
            if cond_type == "quest" and target_ids == [0]:
                continue  # "任意副本" 条件，跳过

            use_and = False
            if cond_type == "trait":
                # condType=1: enemyKillNum (AND), condType=2: enemyIndividualityKillNum (OR)
                # condType=13: allIndividualityInEnemyKillNum (AND)
                mct = detail.get("missionCondType", 0)
                use_and = mct in (1, 13)  # AND 类型

            conds.append({
                "type": cond_type,
                "targetIds": target_ids,
                "useAnd": use_and,
            })

        if conds:
            return {
                "description": cond.get("conditionMessage", em.get("name", "")),
                "count": cond.get("targetNum", 0),
                "conds": conds,
                "condAnd": False,
            }

    return None


# Atlas API missionCondType 到 CustomMissionType 的映射
# 参考 Chaldea event.dart EventMissionCondDetailType enum 值
# 注意：Atlas API 的 enum 值是 Chaldea 源码中定义的值，不是 condType 字段直接对应
_COND_TYPE_MAP = {
    1: "trait",          # enemyKillNum (traits AND)
    2: "trait",          # enemyIndividualityKillNum (traits OR)
    6: "enemy",          # targetQuestEnemyKillNum
    7: "enemy",          # targetQuestEnemyIndividualityKillNum
    9: "quest",          # questClear
    10: "quest",         # questClearNum
    13: "trait",         # allIndividualityInEnemyKillNum (traits AND)
    14: "enemyClass",    # targetEnemyClassKillNum
    15: "servantClass",  # targetSvtEnemyClassKillNum
    16: "enemyNotServantClass",  # targetEnemyIndividualityClassKillNum
    24: "quest",         # questPhaseClearNum
    28: "questTrait",    # questPhaseClearNumQuestType (副本特性)
}


def _map_cond_type(mct: int) -> Optional[str]:
    return _COND_TYPE_MAP.get(mct)


# SvtClass name -> id 映射
_CLASS_NAME_MAP = {
    "saber": 1, "archer": 2, "lancer": 3, "rider": 4,
    "caster": 5, "assassin": 6, "berserker": 7,
    "shielder": 8, "ruler": 9, "avenger": 10,
    "alterEgo": 11, "moonCancer": 12, "foreigner": 13,
    "pretender": 14, "beast": 15,
}


def _class_name_to_id(name: str) -> int:
    return _CLASS_NAME_MAP.get(name, 0)


def main():
    parser = argparse.ArgumentParser(description="更新副本敌人数据和周常任务数据")
    parser.add_argument("--region", default="CN", choices=["CN", "JP", "NA"], help="服务器区域（默认 CN）")
    parser.add_argument("--quests-only", action="store_true", help="仅更新副本数据")
    parser.add_argument("--missions-only", action="store_true", help="仅更新周常任务")
    parser.add_argument("--source", default="auto", choices=["auto", "atlas", "chaldea"],
                        help="副本数据来源: auto=自动选择, atlas=Atlas Academy API, chaldea=Chaldea数据服务器")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    region = args.region.upper()

    if args.missions_only:
        update_master_missions(region)
    elif args.quests_only:
        if args.source == "chaldea":
            update_quest_enemies_from_chaldea(region)
        elif args.source == "atlas":
            update_quest_enemies(region)
        else:
            # auto: 国服默认用 Chaldea，其他用 Atlas
            if region == "CN":
                update_quest_enemies_from_chaldea(region)
            else:
                update_quest_enemies(region)
    else:
        if args.source == "chaldea":
            update_quest_enemies_from_chaldea(region)
        elif args.source == "atlas":
            update_quest_enemies(region)
        else:
            if region == "CN":
                update_quest_enemies_from_chaldea(region)
            else:
                update_quest_enemies(region)
        update_master_missions(region)

    print("\n[完成]")


if __name__ == "__main__":
    main()
