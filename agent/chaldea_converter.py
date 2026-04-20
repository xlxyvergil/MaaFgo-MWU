"""
Chaldea BattleShareData → BBchannel settings JSON 转换器

向后兼容入口：实际实现已拆分至 agent/chaldea/ 包。
保留此文件以兼容 bbc_action.py 中的 `from chaldea_converter import fetch_and_convert`
以及命令行直接调用 `python chaldea_converter.py`。
"""

import os
import sys

# 确保 chaldea 包可被导入（兼容从任意工作目录运行）
_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from chaldea import fetch_and_convert, validate_bbc_config  # noqa: F401, E402


if __name__ == "__main__":
    import json
    import logging
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(
        description="Chaldea → BBchannel 队伍配置转换器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python chaldea_converter.py --source 94061640
  python chaldea_converter.py --source 17300
  python chaldea_converter.py --source "https://chaldea.center/team?id=17300"
  python chaldea_converter.py --source "data=GH4sI..." --outd ./settings
        """
    )
    parser.add_argument("--source", type=str, required=True, help="关卡ID/队伍ID/URL/压缩数据")
    parser.add_argument("--outd", type=str, default=".", help="输出目录")
    parser.add_argument("--validate", action="store_true", help="转换后验证配置")
    args = parser.parse_args()

    result = fetch_and_convert(args.source, args.outd)

    if result:
        print(f"\n✓ 转换成功: {result}")

        if args.validate:
            filepath = os.path.join(args.outd, result)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    config = json.load(f)
                errors = validate_bbc_config(config)
                if errors:
                    print("\n⚠ 配置验证警告:")
                    for err in errors:
                        print(f"  - {err}")
                else:
                    print("\n✓ 配置验证通过")
            except Exception as e:
                print(f"\n✗ 验证失败: {e}")
    else:
        print("\n✗ 转换失败")
        exit(1)
