"""
BBchannel 配置校验
"""

from typing import List


def validate_bbc_config(config: dict) -> List[str]:
    """
    校验 BBC 配置的完整性和正确性

    返回:
        错误信息列表，空列表表示校验通过
    """
    errors: List[str] = []

    if not isinstance(config, dict):
        errors.append("配置不是有效的字典")
        return errors

    required_fields = ["master_equip", "assistIdx", "assistMode", "usedServant"]
    for field in required_fields:
        if field not in config:
            errors.append(f"缺少必需字段: {field}")

    servant_count = 0
    for i in range(6):
        name = config.get(f"servant_{i}_name")
        if name is not None:
            servant_count += 1
    if servant_count == 0:
        errors.append("没有有效的从者信息")

    assist_idx = config.get("assistIdx")
    if assist_idx is not None and not (0 <= assist_idx <= 2):
        errors.append(f"助战索引超出范围: {assist_idx} (应为 0-2)")

    master_equip = config.get("master_equip")
    if master_equip is not None and not isinstance(master_equip, int):
        errors.append(f"魔术礼装 SN 类型错误: {type(master_equip)}")

    round_count = sum(1 for k in config.keys() if k.endswith("_turns"))
    if round_count == 0:
        errors.append("没有回合配置数据")

    for i in range(1, round_count + 1):
        skill_key = f"round{i}_turn0_skill"
        np_key = f"round{i}_turn0_np"
        if skill_key not in config:
            errors.append(f"第 {i} 回合缺少技能配置: {skill_key}")
        if np_key not in config:
            errors.append(f"第 {i} 回合缺少宝具配置: {np_key}")

    return errors
