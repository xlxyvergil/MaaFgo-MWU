"""
Chaldea 队伍导入 Action

从 Chaldea 分享链接/ID 获取队伍数据并转换为 BBC 配置文件
"""

import os
import sys
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context
import mfaalog

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

import mfaalog


@AgentServer.custom_action("import_chaldea_team")
class ImportChaldeaTeam(CustomAction):
    """
    Chaldea 队伍导入 Action
    
    功能：
    1. 读取 chaldea_import_source 参数（链接/ID/压缩数据）
    2. 调用 chaldea_converter 进行转换
    3. 生成 BBC 配置文件到 settings 目录
    4. 通过 pipeline_override 更新 bbc_team_config 参数
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("使用chaldea队伍")
            if not node_data:
                mfaalog.error("[ImportChaldeaTeam] 无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            chaldea_import_source = attach_data.get('chaldea_import_source', '')
            
            # 验证必需参数
            if not chaldea_import_source:
                mfaalog.error("[ImportChaldeaTeam] 未提供 Chaldea 来源参数")
                return CustomAction.RunResult(success=False)
            
            mfaalog.info(f"[ImportChaldeaTeam] 开始处理 Chaldea 来源: {chaldea_import_source[:50]}...")
            
            # 执行转换
            converted_filename = self._convert_chaldea_to_bbc(chaldea_import_source)
            
            if not converted_filename:
                mfaalog.error("[ImportChaldeaTeam] Chaldea 转换失败")
                return CustomAction.RunResult(success=False)
            
            mfaalog.info(f"[ImportChaldeaTeam] 转换成功，生成配置文件: {converted_filename}")
            
            # 通过 pipeline_override 更新后续节点的 bbc_team_config
            context.override_pipeline({
                "执行BBC任务": {
                    "attach": {
                        "bbc_team_config": converted_filename
                    }
                }
            })
            
            mfaalog.info(f"[ImportChaldeaTeam] 已更新 pipeline，使用配置: {converted_filename}")
            
            return CustomAction.RunResult(success=True)
            
        except Exception as e:
            mfaalog.error(f"[ImportChaldeaTeam] 异常: {e}")
            return CustomAction.RunResult(success=False)
    
    def _convert_chaldea_to_bbc(self, source: str) -> str:
        """
        调用 chaldea_converter 进行转换
        
        返回:
            生成的文件名，失败返回空字符串
        """
        try:
            # 动态导入转换模块
            from chaldea_converter import fetch_and_convert
            
            # 计算 BBC_PATH（与 bbc_start.py 保持一致）
            agent_dir = os.path.dirname(os.path.abspath(__file__))
            bbc_path = os.path.abspath(os.path.join(agent_dir, '..', '..', 'BBchannel'))
            
            # 确保 settings 目录存在
            bbc_settings_dir = os.path.join(bbc_path, 'settings')
            os.makedirs(bbc_settings_dir, exist_ok=True)
            
            mfaalog.info(f"[ImportChaldeaTeam] BBC路径: {bbc_path}")
            mfaalog.info(f"[ImportChaldeaTeam] 输出目录: {bbc_settings_dir}")
            
            # 执行转换
            converted_filename = fetch_and_convert(
                source=source,
                output_dir=bbc_settings_dir,
            )
            
            if converted_filename:
                mfaalog.info(f"[ImportChaldeaTeam] 转换成功: {converted_filename}")
                return converted_filename
            else:
                mfaalog.error("[ImportChaldeaTeam] 转换函数返回空结果")
                return ""
                
        except ImportError as e:
            mfaalog.error(f"[ImportChaldeaTeam] 导入转换模块失败: {e}")
            mfaalog.error("[ImportChaldeaTeam] 请确保 chaldea_converter.py 存在")
            return ""
        except Exception as e:
            mfaalog.error(f"[ImportChaldeaTeam] 转换过程出错: {e}")
            return ""
