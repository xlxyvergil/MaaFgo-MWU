"""BBC 配置处理工具"""
from pathlib import Path
import shutil


def prepare_bbc_team_config(assets_options_dir: Path) -> None:
    """
    准备 BBC 队伍配置文件
    
    在源目录中：
    1. 删除 bbc_team_config.json
    2. 将 bbc_team_config_nomwu.json 重命名为 bbc_team_config.json
    
    Args:
        assets_options_dir: assets/options 目录路径
    """
    src_bbc_config = assets_options_dir / "bbc_team_config.json"
    src_nomwu_config = assets_options_dir / "bbc_team_config_nomwu.json"
    
    if src_bbc_config.exists():
        src_bbc_config.unlink()
    
    if src_nomwu_config.exists():
        src_nomwu_config.rename(assets_options_dir / "bbc_team_config.json")


def copy_options_with_bbc_config(working_dir: Path, install_path: Path) -> None:
    """
    复制 options 目录并处理 BBC 配置
    
    Args:
        working_dir: 工作目录（项目根目录）
        install_path: 安装目标目录
    """
    assets_options = working_dir / "assets" / "options"
    
    if not assets_options.exists():
        return
    
    # 先处理源目录的 BBC 配置
    prepare_bbc_team_config(assets_options)
    
    # 然后复制整个 options 目录
    shutil.copytree(
        assets_options,
        install_path / "options",
        dirs_exist_ok=True,
    )
