"""
周常任务求解器 — 数据模型

移植自 Chaldea:
  - chaldea/lib/app/modules/master_mission/solver/scheme.dart
  - chaldea/lib/models/gamedata/quest.dart
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MissionCond:
    """单个任务条件"""
    type: str               # trait, enemyClass, servantClass, enemyNotServantClass, enemy, quest, questTrait
    target_ids: list[int]
    use_and: bool = False

    @property
    def is_quest_type(self) -> bool:
        return self.type in ("quest", "questTrait")

    @property
    def is_enemy_type(self) -> bool:
        return not self.is_quest_type


@dataclass
class Mission:
    """一条周常任务"""
    count: int              # 目标数量（如 15）
    conds: list[MissionCond]
    cond_and: bool = False  # 多条件间的逻辑关系
    description: str = ""   # 原始描述文本

    @property
    def is_valid(self) -> bool:
        return self.count > 0 and len(self.conds) > 0 and all(len(c.target_ids) > 0 for c in self.conds)


@dataclass
class Enemy:
    """副本中的一个敌人"""
    svt_id: int
    class_id: int
    traits: list[int]       # 特性 ID 列表
    deck: str = "enemy"     # enemy / shift / transform
    is_servant: bool = False

    def has_trait(self, trait_id: int) -> bool:
        return trait_id in self.traits


@dataclass
class QuestPhase:
    """一个 Free 副本"""
    id: int
    name: str
    consume: int            # AP 消耗
    war_id: int
    enemies: list[Enemy] = field(default_factory=list)
    individuality: list[int] = field(default_factory=list)  # 副本特性

    @classmethod
    def from_dict(cls, data: dict) -> "QuestPhase":
        enemies = [
            Enemy(
                svt_id=e.get("svtId", 0),
                class_id=e.get("classId", 0),
                traits=e.get("traits", []),
                deck=e.get("deck", "enemy"),
                is_servant=e.get("isServant", False),
            )
            for e in data.get("enemies", [])
        ]
        return cls(
            id=data["id"],
            name=data.get("name", ""),
            consume=data.get("consume", 0),
            war_id=data.get("warId", 0),
            enemies=enemies,
            individuality=data.get("individuality", []),
        )


@dataclass
class SolveResult:
    """求解结果"""
    plan: dict[int, int]            # {quest_id: 刷本次数}
    total_ap: int = 0
    total_runs: int = 0
    details: Optional[dict] = None  # 每个副本对每个任务的贡献明细
