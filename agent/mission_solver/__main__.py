"""
周常任务求解器 — 命令行入口

用法:
    python -m agent.mission_solver
    python -m agent.mission_solver --progress 308
    python -m agent.mission_solver --date 2026-04-14
"""

import argparse
import logging
import sys
from datetime import date, datetime

from .data_loader import get_current_missions, get_free_quests
from .solver import solve

# 强制 stdout UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="MaaFgo 周常任务求解器")
    parser.add_argument("--region", default="CN", choices=["CN", "JP"], help="服务器区域 (默认 CN)")
    parser.add_argument("--progress", type=int, default=0, help="主线进度 War ID (0=不限)")
    parser.add_argument("--date", type=str, default=None, help="目标日期 YYYY-MM-DD (默认今天)")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    # 解析日期
    target_date = None
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"日期格式错误: {args.date}，应为 YYYY-MM-DD")
            sys.exit(1)

    # 获取当前周常任务
    print(f"=== 周常任务求解器 (region={args.region}) ===\n")

    missions = get_current_missions(args.region, target_date)
    if not missions:
        print("未找到当前周的周常任务数据。")
        print("请确认:")
        print(f"  1. 数据文件存在: assets/resource/Chaldea/master_missions_{args.region}.json")
        print("  2. 当前日期有对应的周常任务")
        sys.exit(1)

    print(f"当前周常任务 ({len(missions)} 条):")
    for i, m in enumerate(missions, 1):
        print(f"  {i}. {m.description} (×{m.count})")

    # 获取候选副本
    quests = get_free_quests(args.region, args.progress)
    if not quests:
        print("\n未找到候选副本数据。")
        print(f"请先运行: python tools/update_quest_data.py --region {args.region}")
        sys.exit(1)

    print(f"\n候选副本: {len(quests)} 个")
    if args.progress > 0:
        print(f"  (主线进度限制: warId <= {args.progress})")

    # 求解
    print("\n求解中...")
    result = solve(quests, missions)

    if not result.plan:
        print("\n无法找到可行方案。可能原因:")
        print("  - 部分任务条件无法被任何副本满足")
        print("  - 数据不完整，请更新副本数据")
        sys.exit(1)

    # 输出结果
    print(f"\n=== 最优方案 (总 AP: {result.total_ap}, 总次数: {result.total_runs}) ===\n")

    quest_map = {q.id: q for q in quests}
    for quest_id, count in sorted(result.plan.items(), key=lambda x: -x[1]):
        quest = quest_map.get(quest_id)
        name = quest.name if quest else str(quest_id)
        ap = quest.consume * count if quest else 0
        detail_str = ""
        if result.details and quest_id in result.details:
            parts = [f"{k}: {int(v)}" for k, v in result.details[quest_id].items()]
            detail_str = f"  → {', '.join(parts)}"
        print(f"  {name}  ×{count}  (AP {ap}){detail_str}")

    print()


if __name__ == "__main__":
    main()
