"""
周常任务求解器 — ILP 求解核心

使用 HiGHS 求解器（通过 highspy 包）求解整数线性规划问题：
    min  c'x
    s.t. Ax >= b
         x >= 0, x ∈ Z

移植自 Chaldea:
  chaldea/lib/app/modules/master_mission/solver/solver.dart:16-53
  chaldea/res/js/glpk_solver.js
"""

import logging
from math import ceil

from .matcher import count_mission_target
from .models import Mission, QuestPhase, SolveResult

logger = logging.getLogger("MissionSolver")


def _build_matrix(quests: list[QuestPhase], missions: list[Mission]) -> list[list[int]]:
    """
    构建贡献矩阵 A[m][n]
    A[j][i] = 副本 i 对任务 j 的贡献值
    """
    matrix = []
    for mission in missions:
        row = [count_mission_target(mission, quest) for quest in quests]
        matrix.append(row)
    return matrix


def solve(quests: list[QuestPhase], missions: list[Mission]) -> SolveResult:
    """
    求解最优刷本方案。

    Args:
        quests: 候选 Free 副本列表
        missions: 当前周常任务列表

    Returns:
        SolveResult 包含最优方案、总 AP、总次数和明细
    """
    # 过滤无效任务
    valid_missions = [m for m in missions if m.is_valid]
    if not valid_missions:
        logger.warning("没有有效的任务条件")
        return SolveResult(plan={})

    # 构建贡献矩阵
    A = _build_matrix(quests, valid_missions)
    m = len(valid_missions)
    n = len(quests)

    # 过滤对所有任务贡献为 0 的副本
    useful_cols = [i for i in range(n) if any(A[j][i] > 0 for j in range(m))]
    if not useful_cols:
        logger.warning("没有副本能完成任何任务")
        return SolveResult(plan={})

    filtered_quests = [quests[i] for i in useful_cols]
    filtered_A = [[A[j][i] for i in useful_cols] for j in range(m)]
    fn = len(filtered_quests)

    logger.info(f"问题规模: {m} 个任务, {fn} 个候选副本 (从 {n} 个过滤)")

    # 调用 HiGHS 求解
    try:
        import highspy
    except ImportError:
        logger.error("highspy 未安装，请运行: pip install highspy")
        raise ImportError("请安装 highspy: pip install highspy")

    h = highspy.Highs()
    h.silent()

    inf = highspy.kHighsInf

    # 添加变量: x_i >= 0, 整数, 目标系数 = AP 消耗
    for i in range(fn):
        h.addVariable(0.0, inf, float(filtered_quests[i].consume))

    # 设置变量为整数类型
    for i in range(fn):
        h.changeColIntegrality(i, highspy.HighsVarType.kInteger)

    # 添加约束: Σ(A[j][i] * x_i) >= b[j]
    for j in range(m):
        indices = [i for i in range(fn) if filtered_A[j][i] > 0]
        values = [float(filtered_A[j][i]) for i in indices]
        if indices:
            h.addRow(float(valid_missions[j].count), inf, len(indices), indices, values)
        else:
            logger.warning(f"任务 '{valid_missions[j].description}' 无匹配副本")

    # 求解
    h.run()
    status = h.getModelStatus()

    if status != highspy.HighsModelStatus.kOptimal:
        logger.warning(f"求解未找到最优解，状态: {status}")
        return SolveResult(plan={})

    # 提取结果
    plan = {}
    total_ap = 0
    total_runs = 0
    details = {}

    sol = h.getSolution()
    col_values = sol.col_value

    for i in range(fn):
        count = int(ceil(col_values[i])) if i < len(col_values) else 0
        if count > 0:
            quest = filtered_quests[i]
            plan[quest.id] = count
            total_runs += count
            total_ap += count * quest.consume

            # 计算明细
            quest_details = {}
            for j, mission in enumerate(valid_missions):
                contribution = filtered_A[j][i] * count
                if contribution > 0:
                    quest_details[mission.description] = contribution
            if quest_details:
                details[quest.id] = quest_details

    return SolveResult(
        plan=plan,
        total_ap=total_ap,
        total_runs=total_runs,
        details=details,
    )
