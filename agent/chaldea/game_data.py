"""
FGO 游戏数据名称解析服务

通过 ID 查询从者/礼装/魔术礼装的中文名称。
三级 fallback: 本地 JSON → Atlas Academy API → "从者_{id}"
"""

import json
import os
import ssl
import urllib.request
import logging
from typing import Optional, Dict

from .servant_types import MYSTIC_CODE_ID_TO_BBC_SN

logger = logging.getLogger(__name__)

ATLAS_API = "https://api.atlasacademy.io"

# 缓存
_servant_name_cache: Dict[int, str] = {}
_equip_name_cache: Dict[int, str] = {}
_bbc_servant_sn_cache: Dict[int, str] = {}  # svtId -> SN 映射
_cache_loaded: bool = False
_cache_load_attempted: bool = False


def _ensure_cache_loaded() -> None:
    """
    确保名称缓存已加载，采用两级策略:

    1. 本地 JSON 数据库 (resource/Chaldea/servant_names_CN.json)
       由 tools/update_chaldea_data.py 生成，完全离线，无需网络。
       运行一次更新脚本后永久有效。

    2. Atlas Academy API (网络备用，30秒超时)
       在本地数据库不存在或为空时触发。

    3. 全部失败 → 使用 fallback 名称 "从者_{svtId}"（BBC 无法识别助战）
    """
    global _cache_loaded, _cache_load_attempted
    if _cache_loaded or _cache_load_attempted:
        return
    _cache_load_attempted = True

    # ---- 第一级: 本地 JSON 数据库 ----
    # Chaldea 数据文件位于 resource/Chaldea/
    _chaldea_dir = os.path.dirname(os.path.abspath(__file__))
    # agent/chaldea/ -> agent/ -> MaaFgo/ -> resource/Chaldea/
    data_dir = os.path.join(_chaldea_dir, "..", "..", "resource", "Chaldea")
    servant_path = os.path.join(data_dir, "servant_names_CN.json")
    equip_path = os.path.join(data_dir, "equip_names_CN.json")

    local_servant_ok = False
    if os.path.exists(servant_path):
        try:
            with open(servant_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, name in raw.items():
                if key.startswith("_"):
                    continue  # 跳过 _readme 等元数据键
                try:
                    _servant_name_cache[int(key)] = name
                except ValueError:
                    pass
            if _servant_name_cache:
                logger.info(f"[Chaldea] 从本地数据库加载从者名称: {len(_servant_name_cache)} 条")
                local_servant_ok = True
            else:
                logger.warning("[Chaldea] 本地从者数据库为空，请运行 tools/update_chaldea_data.py")
        except Exception as e:
            logger.warning(f"[Chaldea] 本地从者数据库加载失败: {e}")
    else:
        logger.warning(f"[Chaldea] 本地从者数据库不存在: {servant_path}，请运行 tools/update_chaldea_data.py")

    if os.path.exists(equip_path):
        try:
            with open(equip_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for key, name in raw.items():
                if key.startswith("_"):
                    continue
                try:
                    _equip_name_cache[int(key)] = name
                except ValueError:
                    pass
            if _equip_name_cache:
                logger.info(f"[Chaldea] 从本地数据库加载礼装名称: {len(_equip_name_cache)} 条")
        except Exception as e:
            logger.warning(f"[Chaldea] 本地礼装数据库加载失败: {e}")

    if local_servant_ok:
        _cache_loaded = True
        return

    # ---- 第二级: Atlas Academy API (需要网络) ----
    logger.info("[Chaldea] 本地数据库不可用，尝试从 Atlas Academy API 联网获取...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    atlas_ok = False
    try:
        url = f"{ATLAS_API}/export/CN/nice_servant.json"
        logger.info(f"[Chaldea] 加载从者名称缓存: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for svt in data:
            svt_id = svt.get("id")
            name = svt.get("name")
            if svt_id is not None and name:
                _servant_name_cache[svt_id] = name
                collection_no = svt.get("collectionNo")
                if collection_no:
                    _bbc_servant_sn_cache[svt_id] = str(collection_no).zfill(3) + "00"
        logger.info(f"[Chaldea] 从者名称缓存加载完成: {len(_servant_name_cache)} 条")
        atlas_ok = True
    except Exception as e:
        logger.warning(f"[Chaldea] Atlas API 从者名称加载失败: {e}")

    try:
        url = f"{ATLAS_API}/export/CN/nice_equip.json"
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        for eq in data:
            eq_id = eq.get("id")
            eq_name = eq.get("name")
            if eq_id is not None and eq_name:
                _equip_name_cache[eq_id] = eq_name
        logger.info(f"[Chaldea] 礼装名称缓存加载完成: {len(_equip_name_cache)} 条")
    except Exception as e:
        logger.warning(f"[Chaldea] Atlas API 礼装名称加载失败: {e}")

    if atlas_ok:
        _cache_loaded = True
        return

    # ---- 第三级: 全部失败 ----
    logger.warning("[Chaldea] 所有名称数据源均失败，将使用 fallback 名称（从者_{svtId}）")
    _cache_loaded = True


def get_servant_name(svt_id: Optional[int]) -> str:
    """获取从者中文名称，缓存未命中时返回 '从者_{svtId}'"""
    if svt_id is None:
        return "从者_未知"
    _ensure_cache_loaded()
    return _servant_name_cache.get(svt_id, f"从者_{svt_id}")


def get_equip_name(ce_id: Optional[int]) -> Optional[str]:
    """获取礼装中文名称，缓存未命中时返回 None"""
    if ce_id is None:
        return None
    _ensure_cache_loaded()
    return _equip_name_cache.get(ce_id)


def get_master_equip_sn(mystic_code_id: int) -> int:
    """将 Chaldea mysticCodeId 转换为 BBC master_equip SN"""
    if not isinstance(mystic_code_id, int) or mystic_code_id < 0:
        logger.warning(f"[Chaldea] 无效的 mysticCodeId: {mystic_code_id}，使用默认值 0")
        return 0
    sn = MYSTIC_CODE_ID_TO_BBC_SN.get(mystic_code_id, 0)
    if mystic_code_id not in MYSTIC_CODE_ID_TO_BBC_SN:
        logger.warning(f"[Chaldea] 未知的 mysticCodeId: {mystic_code_id}，使用默认 SN=0")
    return sn
