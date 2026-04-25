"""
周常任务求解器 — 本地数据加载

数据文件位于 agent/mission_solver/，由 tools/update_quest_data.py 预下载。
运行时完全离线，零网络请求。
"""

import json
import logging
import os
from datetime import datetime, date
from typing import Optional

from .models import QuestPhase, Mission, MissionCond

logger = logging.getLogger("MissionSolver")

# 数据目录: 与当前模块同级目录
RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_json(filename: str) -> dict | list:
    filepath = os.path.join(RESOURCE_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"数据文件不存在: {filepath}\n请先运行: python tools/update_quest_data.py")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_quest_enemies(region: str = "CN") -> dict[int, QuestPhase]:
    """
    加载本地副本敌人数据。

    Returns:
        {quest_id: QuestPhase} 字典
    """
    data = _load_json(f"quest_enemies_{region}.json")
    quests = {}
    for quest_id_str, quest_data in data.items():
        quest = QuestPhase.from_dict(quest_data)
        quests[quest.id] = quest
    logger.info(f"加载了 {len(quests)} 个副本 (region={region})")
    return quests


def load_master_missions_schedule(region: str = "CN") -> list[dict]:
    """
    加载本地周常任务时间表。

    Returns:
        周常任务列表，每项包含 startedAt, endedAt, missions
    """
    return _load_json(f"master_missions_{region}.json")


def _parse_mission(data: dict) -> Mission:
    """从 JSON dict 解析一条任务"""
    conds = []
    for cond_data in data.get("conds", []):
        conds.append(MissionCond(
            type=cond_data.get("type", ""),
            target_ids=cond_data.get("targetIds", []),
            use_and=cond_data.get("useAnd", False),
        ))
    return Mission(
        count=data.get("count", 0),
        conds=conds,
        cond_and=data.get("condAnd", False),
        description=data.get("description", ""),
    )


def get_current_missions(region: str = "CN", target_date: Optional[date] = None) -> list[Mission]:
    """
    获取当前周的周常任务。

    从本地时间表按日期匹配。如果找不到当前周的数据，返回空列表。

    Args:
        region: 服务器区域
        target_date: 目标日期，默认为今天

    Returns:
        当前周的任务列表
    """
    if target_date is None:
        target_date = date.today()

    schedule = load_master_missions_schedule(region)

    # 收集所有匹配当前日期的条目
    matched_entries = []
    for entry in schedule:
        started = _parse_date(entry.get("startedAt", ""))
        ended = _parse_date(entry.get("endedAt", ""))
        if started and ended and started <= target_date <= ended:
            entry_id = entry.get("id", 0)
            mission_count = len(entry.get("missions", []))
            # 跳过常驻新手任务 (id=10001, 时间范围极大)
            if entry_id == 10001 and mission_count > 100:
                logger.debug(f"跳过常驻新手任务: id={entry_id}, {mission_count} missions")
                continue
            matched_entries.append(entry)

    if not matched_entries:
        logger.warning(f"未找到 {target_date} 对应的周常任务数据")
        return []

    # 优先选择周常任务 (id 在 100000-199999 范围内)
    # 100xxx = 周常任务, 200xxx = 活动/特殊任务
    weekly_entries = [e for e in matched_entries if 100000 <= e.get("id", 0) < 200000]
    if weekly_entries:
        # 如果有多个周常任务条目，选择任务数最少的（通常是当前周的）
        selected = min(weekly_entries, key=lambda e: len(e.get("missions", [])))
        logger.info(f"选择周常任务: id={selected.get('id')}, {selected.get('startedAt')} ~ {selected.get('endedAt')}")
    else:
        # 没有周常任务，选择第一个匹配的活动任务
        selected = matched_entries[0]
        logger.info(f"选择活动任务: id={selected.get('id')}, {selected.get('startedAt')} ~ {selected.get('endedAt')}")

    missions = [_parse_mission(m) for m in selected.get("missions", [])]
    valid_missions = [m for m in missions if m.is_valid]
    logger.info(f"匹配到 {len(valid_missions)} 条有效任务")
    return valid_missions


def get_free_quests(region: str = "CN", max_war_id: int = 0) -> list[QuestPhase]:
    """
    获取候选 Free 副本列表。

    Args:
        region: 服务器区域
        max_war_id: 主线进度限制（0 = 不限制）

    Returns:
        Free 副本列表
    """
    all_quests = load_quest_enemies(region)
    quests = list(all_quests.values())

    if max_war_id > 0:
        quests = [q for q in quests if q.war_id <= max_war_id]
        logger.info(f"按进度过滤 (warId <= {max_war_id}): {len(quests)} 个副本")

    # 过滤掉 AP=0 的副本
    quests = [q for q in quests if q.consume > 0]

    return quests


def _parse_date(date_str: str) -> Optional[date]:
    """解析日期字符串 (YYYY-MM-DD)"""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
