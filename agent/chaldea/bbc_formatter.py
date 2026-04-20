"""
Chaldea BattleShareData → BBchannel settings JSON 转换逻辑

纯数据转换，无 IO 无网络调用。
"""

import logging
from typing import Optional, Dict, List, Tuple, Any, Union

from .servant_types import (
    SupportType, ServantInfo, MysticCodeInfo, TeamFormation,
    ORDER_CHANGE_MYSTIC_CODE_IDS,
)
from .game_data import get_servant_name, get_equip_name, get_master_equip_sn

logger = logging.getLogger(__name__)


def _parse_support_type(support_type_str: Optional[str]) -> SupportType:
    """解析助战类型字符串"""
    if not support_type_str:
        return SupportType.NONE
    try:
        return SupportType(support_type_str.lower())
    except ValueError:
        return SupportType.NONE


def _extract_ce_id(svt_info: dict) -> Optional[int]:
    """从从者信息中提取概念礼装 ID（兼容新旧格式）"""
    if not isinstance(svt_info, dict):
        return None

    equip1 = svt_info.get("equip1")
    if isinstance(equip1, dict) and equip1.get("id"):
        return equip1["id"]

    ce_id = svt_info.get("ceId")
    if ce_id is not None:
        return ce_id

    return None


def _convert_svt_info(svt_data: Optional[dict], position: int, is_on_field: bool) -> Optional[ServantInfo]:
    """将 Chaldea 从者数据转换为内部 ServantInfo"""
    if svt_data is None:
        return None
    if not isinstance(svt_data, dict):
        logger.warning(f"[Chaldea] 无效的从者数据类型: {type(svt_data)}")
        return None

    skill_lvs = svt_data.get("skillLvs", [])
    if not isinstance(skill_lvs, list) or len(skill_lvs) < 3:
        skill_lvs = [10, 10, 10]

    append_lvs = svt_data.get("appendLvs", [])
    if not isinstance(append_lvs, list):
        append_lvs = []

    return ServantInfo(
        svt_id=svt_data.get("svtId"),
        name=None,
        lv=svt_data.get("lv", 1),
        skill_lvs=skill_lvs[:3],
        append_lvs=append_lvs,
        td_lv=svt_data.get("tdLv", 1),
        atk_fou=svt_data.get("atkFou", 1000),
        hp_fou=svt_data.get("hpFou", 1000),
        ce_id=_extract_ce_id(svt_data),
        ce_limit_break=svt_data.get("ceLimitBreak", False),
        ce_lv=svt_data.get("ceLv", 0),
        support_type=_parse_support_type(svt_data.get("supportType")),
        is_on_field=is_on_field,
        position=position
    )


def _convert_formation(team_data: dict) -> TeamFormation:
    """转换队伍编成数据"""
    formation = TeamFormation()

    on_field_svts = team_data.get("onFieldSvts", [])
    if isinstance(on_field_svts, list):
        for i in range(3):
            svt_data = on_field_svts[i] if i < len(on_field_svts) else None
            formation.on_field[i] = _convert_svt_info(svt_data, i, True)

    backup_svts = team_data.get("backupSvts", [])
    if isinstance(backup_svts, list):
        for i in range(3):
            svt_data = backup_svts[i] if i < len(backup_svts) else None
            formation.backup[i] = _convert_svt_info(svt_data, i + 3, False)

    mystic_code = team_data.get("mysticCode", {})
    if isinstance(mystic_code, dict):
        formation.mystic_code = MysticCodeInfo(
            mystic_code_id=mystic_code.get("mysticCodeId", 0),
            level=mystic_code.get("level", 10)
        )

    return formation


def _determine_assist_info(formation: TeamFormation) -> Tuple[int, str, Union[str, List[Optional[str]]], List[int]]:
    """确定助战相关信息，返回 (assist_idx, assist_mode, assist_equip, used_servant)"""
    assist_idx = None
    assist_mode = "从者礼装"
    assist_equip: Union[str, List[Optional[str]]] = "迦勒底午茶时光"
    used_servant: List[int] = []

    for i, svt in enumerate(formation.on_field):
        if svt is None:
            continue
        if svt.support_type in (SupportType.FRIEND, SupportType.FIXED):
            assist_idx = i
            if svt.ce_id is not None:
                ce_name = get_equip_name(svt.ce_id)
                if ce_name:
                    assist_equip = ce_name
                    assist_mode = "从者礼装"
        else:
            used_servant.append(i)

    if assist_idx is None:
        assist_idx = 2
        used_servant = [i for i in range(3) if formation.on_field[i] is not None and i != assist_idx]
        if not used_servant:
            used_servant = [0, 1]

    return assist_idx, assist_mode, assist_equip, used_servant


def convert_actions_to_bbc_rounds(
    actions: List[dict],
    delegate: Optional[dict] = None,
    mystic_code_id: int = 0
) -> Dict[str, Any]:
    """
    将 Chaldea actions 转换为 BBC 回合配置

    技能编号映射:
    - 1-3: 从者0的技能1-3
    - 4-6: 从者1的技能1-3
    - 7-9: 从者2的技能1-3
    - 10-12: 御主技能1-3
    """
    rounds_config: Dict[str, Any] = {}
    current_skills: List[Union[int, List]] = []
    current_nps: List[int] = []
    round_idx = 1
    turn_idx = 0

    replace_members: List[List[int]] = []
    if isinstance(delegate, dict):
        replace_members = delegate.get("replaceMemberIndexes", [])
    replace_ptr = 0

    is_order_change = (mystic_code_id in ORDER_CHANGE_MYSTIC_CODE_IDS)

    for action in actions:
        if not isinstance(action, dict):
            continue

        action_type = action.get("type", "")

        if action_type == "skill":
            svt_idx = action.get("svt")
            skill_idx = action.get("skill", 0)
            options = action.get("options", {}) or {}

            if svt_idx is None:
                bbc_skill_idx = 10 + skill_idx

                if bbc_skill_idx == 12 and is_order_change:
                    if replace_ptr < len(replace_members):
                        backup_idx = replace_members[replace_ptr][1] + 1
                        current_skills.append([-2, backup_idx])
                        replace_ptr += 1
                    else:
                        logger.warning("[Chaldea] 换人技能数量超过 delegate 中定义的换人信息")
                        current_skills.append([-2, 1])
                    continue
            else:
                if not isinstance(svt_idx, int) or not (0 <= svt_idx <= 5):
                    logger.warning(f"[Chaldea] 无效的从者索引: {svt_idx}")
                    continue
                bbc_skill_idx = svt_idx * 3 + skill_idx + 1

            player_target = options.get("playerTarget")
            if player_target is not None and isinstance(player_target, int) and player_target >= 0:
                current_skills.append([bbc_skill_idx, player_target + 1])
            else:
                current_skills.append(bbc_skill_idx)

        elif action_type == "attack":
            attacks = action.get("attacks", [])
            if isinstance(attacks, list):
                for atk in attacks:
                    if not isinstance(atk, dict):
                        continue
                    if atk.get("isTD", False):
                        svt_pos = atk.get("svt", 0)
                        if isinstance(svt_pos, int) and 0 <= svt_pos <= 5:
                            np_pos = svt_pos + 1
                            if np_pos not in current_nps:
                                current_nps.append(np_pos)

            rounds_config[f"round{round_idx}_turns"] = 1
            rounds_config[f"round{round_idx}_extraSkill"] = []
            rounds_config[f"round{round_idx}_extraStrategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_skill"] = current_skills.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_np"] = current_nps.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_strategy"] = None
            rounds_config[f"round{round_idx}_turn{turn_idx}_condition"] = None

            current_skills = []
            current_nps = []
            round_idx += 1

    return rounds_config


def chaldea_to_bbc(share_data: dict) -> dict:
    """主转换函数: Chaldea BattleShareData → BBchannel 配置 dict"""
    if not isinstance(share_data, dict):
        logger.error("[Chaldea] share_data 类型错误，期望 dict")
        return {}

    team_data = share_data.get("team", {})
    actions = share_data.get("actions", [])
    delegate = share_data.get("delegate") or {}

    if not isinstance(team_data, dict):
        logger.error("[Chaldea] team 字段类型错误")
        return {}

    formation = _convert_formation(team_data)
    mystic_code_id = formation.mystic_code.mystic_code_id

    assist_idx, assist_mode, assist_equip, used_servant = _determine_assist_info(formation)

    result: Dict[str, Any] = {
        "_source": "chaldea",
        "_questId": (share_data.get("quest") or {}).get("id"),
        "_appBuild": share_data.get("appBuild"),
    }

    all_svts = list(formation.on_field) + list(formation.backup)
    for i in range(6):
        svt = all_svts[i] if i < len(all_svts) else None
        if svt is not None and svt.svt_id is not None:
            result[f"servant_{i}_name"] = get_servant_name(svt.svt_id)
        else:
            result[f"servant_{i}_name"] = None

    result["assistMode"] = assist_mode
    result["assistIdx"] = assist_idx
    result["assistEquip"] = assist_equip
    result["master_equip"] = get_master_equip_sn(mystic_code_id)
    result["usedServant"] = used_servant

    result["connectMode"] = "ADB方式"
    result["snapshotDevice"] = ["normal", "127.0.0.1:7555"]
    result["operateDevice"] = ["normal", "127.0.0.1:7555"]
    result["specialKeys"] = []

    if isinstance(actions, list):
        bbc_actions = convert_actions_to_bbc_rounds(actions, delegate, mystic_code_id)
        result.update(bbc_actions)
    else:
        logger.warning("[Chaldea] actions 字段不是列表，跳过战斗逻辑转换")

    return result
