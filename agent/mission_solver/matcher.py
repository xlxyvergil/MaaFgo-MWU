"""
周常任务求解器 — 条件匹配

移植自 Chaldea MissionSolver.countMissionTarget():
  chaldea/lib/app/modules/master_mission/solver/solver.dart:56-120

Chaldea 中 Trait.servant = 1000 (从者特性), Trait.notBasedOnServant = 2666
"""

from .models import Mission, MissionCond, QuestPhase, Enemy

# Chaldea 特性常量
TRAIT_SERVANT = 1000           # Trait.servant
TRAIT_NOT_BASED_ON_SERVANT = 2666  # Trait.notBasedOnServant


def _has_any_trait(traits: list[int], target_ids: list[int]) -> bool:
    return any(t in traits for t in target_ids)


def _has_all_traits(traits: list[int], target_ids: list[int]) -> bool:
    return all(t in traits for t in target_ids)


def _match_cond_quest(cond: MissionCond, quest: QuestPhase) -> bool:
    """匹配副本类型条件"""
    if cond.type == "quest":
        return quest.id in cond.target_ids
    elif cond.type == "questTrait":
        if cond.use_and:
            return _has_all_traits(quest.individuality, cond.target_ids)
        else:
            return _has_any_trait(quest.individuality, cond.target_ids)
    return False


def _match_cond_enemy(cond: MissionCond, enemy: Enemy) -> bool:
    """匹配敌人类型条件"""
    if cond.type == "trait":
        if cond.use_and:
            return _has_all_traits(enemy.traits, cond.target_ids)
        else:
            return _has_any_trait(enemy.traits, cond.target_ids)
    elif cond.type == "enemy":
        return enemy.svt_id in cond.target_ids
    elif cond.type == "enemyClass":
        return enemy.class_id in cond.target_ids
    elif cond.type == "servantClass":
        return TRAIT_SERVANT in enemy.traits and enemy.class_id in cond.target_ids
    elif cond.type == "enemyNotServantClass":
        return TRAIT_NOT_BASED_ON_SERVANT in enemy.traits and enemy.class_id in cond.target_ids
    return False


def count_mission_target(mission: Mission, quest: QuestPhase) -> int:
    """
    计算一次刷副本对任务的贡献值。

    副本类型条件（quest, questTrait）：匹配则贡献 1
    敌人类型条件（trait, enemyClass 等）：遍历敌人，累计匹配数

    Args:
        mission: 一条周常任务
        quest: 一个 Free 副本

    Returns:
        贡献值（整数）
    """
    if not mission.conds:
        return 0

    # 按第一个条件判断是副本类型还是敌人类型
    if mission.conds[0].is_quest_type:
        # 副本类型条件：检查副本是否匹配
        results = [_match_cond_quest(cond, quest) for cond in mission.conds]
        if mission.cond_and:
            matched = all(results)
        else:
            matched = any(results)
        return 1 if matched else 0
    else:
        # 敌人类型条件：遍历副本中所有敌人
        count = 0
        for enemy in quest.enemies:
            if enemy.deck != "enemy":
                continue
            results = [_match_cond_enemy(cond, enemy) for cond in mission.conds]
            if mission.cond_and:
                if all(results):
                    count += 1
            else:
                if any(results):
                    count += 1
        return count
