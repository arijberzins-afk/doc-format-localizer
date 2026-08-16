#!/usr/bin/env python3
"""
格式保留本地化：在原图/PDF上直接覆盖替换省份、机关等短词，保留原始版面。

用法:
  python localize_preserve_format.py \
    --input 文件.jpg \
    --target-authority "福建省人力资源和社会保障厅" \
    --target-province "福建省" \
    --config path/to/config.json

  python localize_preserve_format.py \
    --input 文件.pdf \
    --target-authority "福建省人力资源和社会保障厅" \
    --target-province "福建省" \
    --config path/to/config.json
"""

import argparse
import base64
import json
import os
import re
import sys
from pathlib import Path

# 必须在 paddle 导入前设置，禁用 OneDNN/MKL-DNN 避免 CPU 推理崩溃
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_mkldnn_cache_capacity"] = "0"

import time

import requests

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False

try:
    import cv2 as _cv2
    import numpy as _cv2_np
    CV2_OK = True
except ImportError:
    CV2_OK = False

try:
    from skimage.metrics import structural_similarity as _ssim_fn
    SKIMAGE_OK = True
except ImportError:
    SKIMAGE_OK = False

try:
    from paddleocr import PaddleOCR as _PaddleOCR
    _paddle_instance = None
    def get_easy_reader():
        global _paddle_instance
        if _paddle_instance is None:
            _paddle_instance = _PaddleOCR(
                use_angle_cls=False,
                lang='ch',
                det_model_dir='C:/paddleocr/det',
                rec_model_dir='C:/paddleocr/rec',
                cls_model_dir='C:/paddleocr/cls',
                use_gpu=False,
                enable_mkldnn=False,
                show_log=False,
            )
        return _paddle_instance
    PADDLE_OK = True
except (ImportError, TypeError, Exception):
    PADDLE_OK = False
    def get_easy_reader():
        return None

try:
    import fitz  # PyMuPDF
    FITZ_OK = True
except ImportError:
    try:
        import pymupdf as fitz
        FITZ_OK = True
    except ImportError:
        FITZ_OK = False

# ─────────────────────────────────────────────
# 字体映射（系统回退字体）
# ─────────────────────────────────────────────

_ARIALUNI = "C:/develop/arialuni.ttf"

# 渲染匹配：只对这些区域执行 SSIM 对比选字体
RENDER_MATCH_REGIONS = {"red_header", "title", "heading1", "heading2", "heading3"}

# 候选字体：渲染匹配时从中选 SSIM 最高者
# 顺序无关，match_font_by_render 会全部试一遍
CANDIDATE_FONTS = [
    "C:/Users/和稀泥的针尾鱼/Downloads/fangzhengxiaobiaosongjian/方正小标宋简/方正小标宋简.TTF",  # 方正小标宋简体（红头首选）
    "C:/Users/和稀泥的针尾鱼/Downloads/fangzhengxiaobiaosong_GBK/方正小标宋_GBK/方正小标宋_GBK.TTF",  # 方正小标宋GBK
    _ARIALUNI,                              # Arial Unicode MS（38917字符兜底）
    "C:/Windows/Fonts/STZHONGS.TTF",        # 华文中宋
    "C:/Users/和稀泥的针尾鱼/Downloads/3e3ef-main/3e3ef-main/simsun/simsun/simsun.ttf",  # 宋体
    "C:/Windows/Fonts/simsunb.ttf",         # 宋体粗体
    "C:/Windows/Fonts/STSONG.TTF",          # 华文宋体
    "C:/Windows/Fonts/simhei.ttf",          # 黑体（标题）
    "C:/Windows/Fonts/simfang.ttf",         # 仿宋_GB2312
    "C:/Windows/Fonts/STFANGSO.TTF",        # 华文仿宋
    "C:/Windows/Fonts/simkai.ttf",          # 楷体_GB2312
    "C:/Windows/Fonts/STKAITI.TTF",         # 华文楷体
    "C:/Windows/Fonts/times.ttf",           # Times New Roman
    "C:/Windows/Fonts/timesbd.ttf",         # Times New Roman Bold
]

# 字体匹配缓存：{region_label: (font_path, ssim_score)}
font_match_cache: dict = {}

_FZXBS_GBK  = "C:/Users/和稀泥的针尾鱼/Downloads/fangzhengxiaobiaosong_GBK/方正小标宋_GBK/方正小标宋_GBK.TTF"
_FZXBS_JIAN = "C:/Users/和稀泥的针尾鱼/Downloads/fangzhengxiaobiaosongjian/方正小标宋简/方正小标宋简.TTF"

FONT_MAP = {
    # Arial Unicode MS
    "ArialUnicodeMS":              _ARIALUNI,
    "Arial Unicode MS":            _ARIALUNI,
    # 仿宋
    "FangSong_GB2312":             "C:/Windows/Fonts/simfang.ttf",
    "FangSong":                    "C:/Windows/Fonts/simfang.ttf",
    "仿宋":                        "C:/Windows/Fonts/simfang.ttf",
    "仿宋_GB2312":                 "C:/Windows/Fonts/simfang.ttf",
    "STFangsong":                  "C:/Windows/Fonts/STFANGSO.TTF",
    "华文仿宋":                    "C:/Windows/Fonts/STFANGSO.TTF",
    # 黑体
    "SimHei":                      "C:/Windows/Fonts/simhei.ttf",
    "黑体":                        "C:/Windows/Fonts/simhei.ttf",
    # 楷体
    "KaiTi_GB2312":                "C:/Windows/Fonts/simkai.ttf",
    "KaiTi":                       "C:/Windows/Fonts/simkai.ttf",
    "楷体":                        "C:/Windows/Fonts/simkai.ttf",
    "楷体_GB2312":                 "C:/Windows/Fonts/simkai.ttf",
    "STKaiti":                     "C:/Windows/Fonts/STKAITI.TTF",
    "华文楷体":                    "C:/Windows/Fonts/STKAITI.TTF",
    # 宋体
    "SimSun":                      "C:/Users/和稀泥的针尾鱼/.claude/skills/doc-format-localizer/fonts/SimSun.ttf",
    "宋体":                        "C:/Users/和稀泥的针尾鱼/.claude/skills/doc-format-localizer/fonts/SimSun.ttf",
    "SimSun-ExtB":                 "C:/Users/和稀泥的针尾鱼/.claude/skills/doc-format-localizer/fonts/SimSun.ttf",
    "SimSunBold":                  "C:/Windows/Fonts/simsunb.ttf",
    "宋体粗体":                    "C:/Windows/Fonts/simsunb.ttf",
    "STSong":                      "C:/Windows/Fonts/STSONG.TTF",
    "华文宋体":                    "C:/Windows/Fonts/STSONG.TTF",
    # Times New Roman（用于标题中的西文数字）
    "TimesNewRoman":               "C:/Windows/Fonts/times.ttf",
    "Times New Roman":             "C:/Windows/Fonts/times.ttf",
    "TimesNewRomanPS-BoldMT":      "C:/Windows/Fonts/timesbd.ttf",
    # 华文中宋
    "STZhongsong":                 "C:/Windows/Fonts/STZHONGS.TTF",
    "华文中宋":                    "C:/Windows/Fonts/STZHONGS.TTF",
    # 方正小标宋（GBK 版优先；嵌入子集名 FZXBSJW / FZXBSK 等均映射到此）
    "FZXBSJW":                     _FZXBS_GBK,
    "FZXBSK":                      _FZXBS_GBK,
    "FZXiaoBiaoSong-R-GBK":        _FZXBS_GBK,
    "方正小标宋":                  _FZXBS_GBK,
    "方正小标宋_GBK":              _FZXBS_GBK,
    "方正小标宋简体":              _FZXBS_JIAN,
    "FZXiaoBiaoSong-R-JT":         _FZXBS_JIAN,
    # Times New Roman
    "TimesNewRomanPSMT":           "C:/Windows/Fonts/times.ttf",
    "TimesNewRomanPS-BoldMT":      "C:/Windows/Fonts/timesbd.ttf",
    "TimesNewRomanPS-ItalicMT":    "C:/Windows/Fonts/timesi.ttf",
    "TimesNewRomanPS-BoldItalicMT":"C:/Windows/Fonts/timesbi.ttf",
    "Times New Roman":             "C:/Windows/Fonts/times.ttf",
}

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


# ─────────────────────────────────────────────
# 配置加载
# ─────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    if not config_path or not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def get_api_key(config: dict) -> str:
    return (
        os.environ.get("QWEN_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or config.get("qwen_api_key")
        or config.get("llm_api_key")
        or ""
    )


def get_endpoint(config: dict) -> str:
    ep = (
        config.get("qwen_api_endpoint")
        or config.get("qwen_endpoint")
        or config.get("llm_api_endpoint")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    ep = ep.rstrip("/")
    return ep if ep.endswith("/chat/completions") else f"{ep}/chat/completions"


def get_vision_model(config: dict) -> str:
    return config.get("source_vision_model") or config.get("qwen_vision_model") or "qwen-vl-max"


def get_vision_endpoint(config: dict) -> str:
    """
    视觉 API endpoint 独立于文本 LLM endpoint。
    优先读 qwen_vision_api_endpoint；若未设置，检查 qwen_api_endpoint 是否为千问官方或兼容地址；
    否则回退到千问官方 DashScope 地址。
    """
    # 1. 显式视觉专用 endpoint
    ep = config.get("qwen_vision_api_endpoint") or config.get("vision_api_endpoint")
    if ep:
        ep = ep.rstrip("/")
        return ep if ep.endswith("/chat/completions") else f"{ep}/chat/completions"

    # 2. 若 qwen_api_endpoint / qwen_endpoint 像千问/DashScope 地址，沿用
    qep = config.get("qwen_api_endpoint") or config.get("qwen_endpoint") or ""
    if qep and ("dashscope" in qep or "aliyun" in qep or "qianwen" in qep):
        qep = qep.rstrip("/")
        return qep if qep.endswith("/chat/completions") else f"{qep}/chat/completions"

    # 3. 回退千问官方 DashScope OpenAI 兼容地址
    return "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def get_llm_model(config: dict) -> str:
    return config.get("qwen_model") or config.get("llm_model") or "qwen-max"


def get_proxies(config: dict):
    return {"http": None, "https": None} if config.get("llm_disable_proxy") else None


# ─────────────────────────────────────────────
# API 调用工具
# ─────────────────────────────────────────────

def call_llm(messages: list, config: dict, model: str = None, temperature: float = 0) -> str:
    url = get_endpoint(config)
    api_key = get_api_key(config)
    model = model or get_llm_model(config)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4000,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=(15, 120),
        proxies=get_proxies(config),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def call_vision(image_bytes: bytes, mime: str, prompt: str, config: dict) -> str:
    url = get_vision_endpoint(config)
    api_key = get_api_key(config)
    model = get_vision_model(config)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }],
        "temperature": 0,
        "max_tokens": 6000,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=(15, 180),
        proxies=get_proxies(config),
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ─────────────────────────────────────────────
# 替换词表生成（两步：提取结构化字段 → 地域替换 → diff得到词表）
# 逻辑直接借鉴 gov-doc-generator/scripts/localize_document.py
# ─────────────────────────────────────────────

# 自治区/民族后缀简称映射：全称 → 公文常用简称
_PROVINCE_SHORTNAME = {
    "内蒙古自治区": "内蒙古",
    "广西壮族自治区": "广西",
    "西藏自治区": "西藏",
    "新疆维吾尔自治区": "新疆",
    "宁夏回族自治区": "宁夏",
    # 直辖市省级写法统一
    "北京市": "北京",
    "上海市": "上海",
    "天津市": "天津",
    "重庆市": "重庆",
}


def shorten_province(name: str) -> str:
    """把省份全称转为公文常用简称，避免写入时字数过多撑版面。"""
    return _PROVINCE_SHORTNAME.get(name, name)


MAPPING_SYSTEM = """你是一个政府公文地域替换助手。

从用户提供的公文原文中，找出省市级地名，输出替换词表。

**核心规则：只替换省市地名本身，不带任何后缀（区/县/局/厅/委/处/办/院/部等）。**

替换规则（只有以下两类）：

1. 省份名/直辖市名 → 目标省份名（目标省份将在用户消息中给出）
   ✓ 正确示例（假设目标省份为"X省"）：
     "广东省" → "X省"
     "天津市" → "X省"
     "重庆市" → "X省"
     "南京市" → "X省"
   ✗ 错误示例（不要输出）：
     "重庆高新区" → 错误，"高新区"是行政区后缀，只该输出"重庆市"
     "重庆高新区生态环境局" → 错误，带了机构名
     "广东省人力资源和社会保障厅" → 错误，带了机构名

2. 发文字号中的省市简称单字 → 目标省简称（目标省简称将在用户消息中给出）
   ✓ 正确示例（假设目标省简称为"X"）：
     "粤人社规〔2021〕5号" 中 → {"粤": "X"}（只改"粤"这一个字）
     "宁发改财金字〔2018〕××号" 中 → {"宁": "X"}

绝对不替换：
- 企业名称、项目名称中的地名
- 正文叙述中的历史地名（如"与广东省签订协议"不应改）
- URL、电话、编号
- 机关职能后缀（局/厅/委/处/办/院/部）

每条原词必须可在原文中直接搜索到，不含换行符，长度不超过10字。
只输出纯 JSON 对象，格式：{"原词": "新词"}，不加任何解释。"""


def _call_llm_json(system_prompt: str, user_content: str, config: dict) -> dict:
    """调用 LLM，期望返回 JSON 对象，最多重试3次。"""
    url = get_endpoint(config)
    api_key = get_api_key(config)
    model = get_llm_model(config)
    proxies = get_proxies(config)
    max_retries = int(config.get("llm_max_retries", 3))

    for attempt in range(1, max_retries + 1):
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if "qwen3" in model.lower() or "qwen2" in model.lower():
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=(15, int(config.get("llm_read_timeout_seconds", 120))),
                proxies=proxies,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*", "", content.strip())
            content = re.sub(r"\s*```$", "", content.strip())
            return json.loads(content)
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            if attempt == max_retries:
                raise RuntimeError(f"LLM 调用失败（第{attempt}次）: {e}")
            wait = 2 ** attempt
            print(f"  LLM 调用失败，{wait}s 后重试（{attempt}/{max_retries}）: {e}", file=sys.stderr)
            time.sleep(wait)


def _find_minimal_diff(orig: str, new: str, mapping: dict):
    pass  # 保留占位，不再使用


def _extract_word_pairs(orig_val: str, new_val: str, mapping: dict):
    pass  # 保留占位，不再使用


def build_replacement_mapping(raw_text: str, src_province: str,
                               tgt_province: str, tgt_authority: str,
                               config: dict,
                               src_authorities: list = None) -> dict:
    """
    让 LLM 直接从原文提取"省份名/机关名/发文字号前缀"这三类短词的替换词表。
    每条原词必须是可在原文中直接搜索到的完整词组，长度≤25字符。
    src_authorities: 已识别的原发文机关列表（可多个），用于在 prompt 中明确告知 LLM。
    """
    # 构造发文机关提示行
    if src_authorities and len(src_authorities) > 0:
        auth_hint = "原发文机关（已识别）：" + "、".join(src_authorities) + "\n"
    else:
        auth_hint = ""

    # 多机关时额外提示：目标机关只是参考，各机关按职能各自对口替换
    if src_authorities and len(src_authorities) > 1:
        auths_joined = "、".join(src_authorities)
        multi_auth_note = (
            f"\n【重要】本文有{len(src_authorities)}个联合发文机关：{auths_joined}。"
            f"每个机关只把省市地点前缀改为\u300c{tgt_province}\u300d，职能后缀原样保留，不要把不同职能的机关名混淆合并。"
            f"例如：城市管理局\u2192{tgt_province}城市管理局，规划局\u2192{tgt_province}规划局，不要都变成{tgt_authority}。"
        )
    else:
        multi_auth_note = ""

    user_prompt = (
        f"公文原文：\n\n{raw_text[:4000]}\n\n"
        f"{auth_hint}"
        f"目标省份：{tgt_province}\n"
        f"目标机关（参考）：{tgt_authority}\n\n"
        f"请输出替换词表，只替换省份名、机关名和发文字号前缀这三类短词。"
        + multi_auth_note
    )
    try:
        result = _call_llm_json(MAPPING_SYSTEM, user_prompt, config)
    except Exception as e:
        print(f"  LLM 词表生成失败: {e}", file=sys.stderr)
        return {}

    if not isinstance(result, dict):
        return {}

    # 常见省市名前缀，用于判断原词是否含有可替换的地点成分
    _PROVINCE_CITY_PREFIXES = (
        '北京', '天津', '上海', '重庆', '河北', '山西', '辽宁', '吉林', '黑龙江',
        '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南',
        '广东', '广西', '海南', '四川', '贵州', '云南', '陕西', '甘肃', '青海',
        '内蒙古', '西藏', '新疆', '宁夏', '香港', '澳门', '台湾',
        '南京', '杭州', '宁波', '武汉', '成都', '西安', '沈阳', '哈尔滨',
        '济南', '青岛', '厦门', '深圳', '广州', '长沙', '郑州', '合肥', '石家庄',
    )

    # 过滤：原词必须在原文中可搜索到，长度≤25，不含换行
    mapping = {}
    for k, v in result.items():
        if not (k and v and k != v and "\n" not in k and k in raw_text):
            continue
        if len(k) > 25:
            continue
        # 防止 LLM 把无地点前缀的机关缩写（如"省发展改革委"）整体映射为目标机关全称
        # 规则：若新词等于目标机关全称，但原词不以任何已知省/市名开头，则丢弃
        if v == tgt_authority and not any(k.startswith(p) for p in _PROVINCE_CITY_PREFIXES):
            continue
        mapping[k] = v

    print(f"  词表生成 {len(mapping)} 条替换对")

    return mapping


# ─────────────────────────────────────────────
# 图片流程
# ─────────────────────────────────────────────

FONT_DETECT_PROMPT = """请识别这张中国政府公文图片中每一行文字的内容、位置和字体信息。

图片宽度为 {img_w} 像素，高度为 {img_h} 像素。

【重要】以行为单位，每一行文字单独返回一条记录，不要把多行合并成一条。

返回 JSON 列表，每行一条：
[
  {{
    "text": "这一行的完整文字",
    "x1": 左边界像素,
    "y1": 顶边界像素,
    "x2": 右边界像素,
    "y2": 底边界像素,
    "font": "字体名称（从：方正小标宋, 黑体, 仿宋_GB2312, 楷体, 宋体 中选一个）",
    "bold": true或false,
    "color": "red或black",
    "region": "区域类型（从：red_header, doc_number, title, body, imprint 中选一个）"
  }}
]

字体识别规律：
- 顶部红色大字版头 → 方正小标宋, red, bold, region=red_header
- 发文字号（如"津市场监管审批〔2020〕1号"）→ 仿宋_GB2312, bold=false, region=doc_number
  **重要**：发文字号必须包含完整内容，特别是末尾的"号"字，x2坐标要确保包含"号"字的右边界
- 正文标题（一、二、三、等加粗行）→ 黑体, bold=true, region=title
- 正文每行 → 仿宋_GB2312, region=body
- 落款、成文日期 → 仿宋_GB2312, region=body
- 页面最底部版记：印发单位和印发日期（如"XX办公室  2020年2月3日印发"）→ 仿宋_GB2312, region=imprint

注意：
1. x1/y1/x2/y2 是该行文字在图片中的实际像素坐标，尽量精确到行高
2. 发文字号的x2必须包含最后的"号"字，不能截断
只输出JSON数组，不加任何解释。"""


def infer_font_by_position(y_ratio: float, is_bold: bool) -> str:
    if y_ratio < 0.12:
        return "方正小标宋"
    if is_bold:
        return "SimHei"
    return "仿宋_GB2312"


def get_font_size_for_line(is_red: bool, is_title: bool, box_h: int) -> int:
    """
    正文固定 16pt 已废弃；改用 fit_font_to_height 精确拟合行高。
    此函数仅作保留兼容，调用方应优先使用 fit_font_to_height。
    """
    if is_red:
        return max(int(box_h * 0.95), 10)
    if is_title:
        return max(int(box_h * 1.00), 10)
    return max(int(box_h * 1.00), 10)


def fit_font_to_height(font_path: str, target_h: int,
                       mode: str = "body") -> "ImageFont.FreeTypeFont":
    """
    二分搜索找到渲染可见高度最接近 target_h 的字号。
    用多个样本字取平均高度，减少单字误差。
    mode: 'red'（红头大字）/ 'title'（标题）/ 'body'（正文）
    """
    from PIL import ImageFont as _IF, Image as _Img, ImageDraw as _Draw

    # 按区域取有代表性的样本字：公文里实际会出现的汉字
    SAMPLES = {
        "red":   "福建省人力资源",   # 红头：仿宋大字，横向笔画多
        "title": "关于开展工作通知",  # 标题：黑体，笔画均匀
        "body":  "根据有关规定现将",  # 正文：仿宋，典型正文字
    }
    sample_text = SAMPLES.get(mode, "国家机关文件")

    tmp = _Img.new("RGB", (500, 200))
    drw = _Draw.Draw(tmp)

    lo, hi = 6, max(target_h * 3, 80)
    best_size = lo
    for _ in range(25):
        mid = (lo + hi) // 2
        try:
            f = _IF.truetype(font_path, mid)
        except Exception:
            return _IF.load_default()
        # 逐字测量取平均，消除单字偏差
        heights = []
        for ch in sample_text:
            bb = drw.textbbox((0, 0), ch, font=f)
            h = bb[3] - bb[1]
            if h > 0:
                heights.append(h)
        rendered_h = sum(heights) / len(heights) if heights else 1
        if rendered_h <= target_h:
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    try:
        return _IF.truetype(font_path, best_size)
    except Exception:
        return _IF.load_default()


def fit_font_to_width(font_path: str, text: str, target_w: int,
                      mode: str = "red") -> tuple:
    """
    二分搜索找到使 text 渲染宽度最接近 target_w 的字号。
    返回 (font_obj, rendered_w, rendered_h)。
    用于红头纵向拉伸校正：先按宽度定字号，再算高度差得 scale_y。
    """
    from PIL import ImageFont as _IF, Image as _Img, ImageDraw as _Draw
    tmp = _Img.new("RGB", (max(target_w * 3, 200), 200))
    drw = _Draw.Draw(tmp)

    lo, hi = 6, 300
    best_size = lo
    best_font = None
    for _ in range(25):
        mid = (lo + hi) // 2
        try:
            f = _IF.truetype(font_path, mid)
        except Exception:
            return _IF.load_default(), target_w, 20
        bb = drw.textbbox((0, 0), text, font=f)
        w = bb[2] - bb[0]
        if w <= target_w:
            best_size = mid
            best_font = f
            lo = mid + 1
        else:
            hi = mid - 1
        if lo > hi:
            break
    if best_font is None:
        try:
            best_font = _IF.truetype(font_path, best_size)
        except Exception:
            return _IF.load_default(), target_w, 20
    bb = drw.textbbox((0, 0), text, font=best_font)
    return best_font, max(bb[2] - bb[0], 1), max(bb[3] - bb[1], 1)


def render_red_header_per_char(
        img: "Image.Image",
        img_arr,
        orig_text: str,
        new_text: str,
        font_path: str,
        bx1: int, by1: int, bx2: int, by2: int,
        fill: tuple, bg_color: tuple,
) -> None:
    """
    红头逐字替换：
    1. 颜色掩码 + findContours 逐字定位原图每个字的 bbox
    2. 用第一个字估算原始字号和纵向拉伸比例 scale_y
    3. 新文字逐字渲染（字号不变，不做水平缩放），纵向拉伸 scale_y 后按原字间距贴回
    字数相同：直接用原字 bbox 的左边缘 / 中心定位
    字数不同：固定总宽度，均匀重新分配字间距
    """
    import cv2 as _cv2
    import numpy as _np
    from PIL import Image as _Img, ImageDraw as _Draw, ImageFont as _IF

    # ── 1. 逐字分割 ──
    region = _np.array(img)[max(by1,0):min(by2,img.size[1]),
                             max(bx1,0):min(bx2,img.size[0])]
    if region.size == 0:
        return

    # 红色掩码
    r, g, b = region[:,:,0].astype(float), region[:,:,1].astype(float), region[:,:,2].astype(float)
    red_mask = ((r > 120) & (g < r * 0.6) & (b < r * 0.6)).astype(_np.uint8) * 255
    # 形态学闭运算，连接断笔
    kernel = _np.ones((3,3), _np.uint8)
    red_mask = _cv2.morphologyEx(red_mask, _cv2.MORPH_CLOSE, kernel)

    contours, _ = _cv2.findContours(red_mask, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        # 回退：整行白底擦除后均分
        _fallback = render_stretched_text(new_text, font_path, bx2-bx1, by2-by1, fill, bg_color)
        img.paste(_fallback, (bx1, by1))
        return

    # 转为 (abs_x1, abs_y1, abs_x2, abs_y2)，按 x 排序
    boxes = []
    total_area = red_mask.shape[0] * red_mask.shape[1]
    for cnt in contours:
        x, y, w, h = _cv2.boundingRect(cnt)
        area = _cv2.contourArea(cnt)
        if area < total_area * 0.003 or w < 3 or h < 3:
            continue
        boxes.append([bx1 + x, by1 + y, bx1 + x + w, by1 + y + h])
    boxes.sort(key=lambda b: b[0])

    # 合并 x 轴距离 < avg_w*0.3 的相邻轮廓（同一字的断笔）
    if boxes:
        avg_w = sum(b[2]-b[0] for b in boxes) / len(boxes)
        merged = [boxes[0]]
        for b in boxes[1:]:
            prev = merged[-1]
            if b[0] - prev[2] < avg_w * 0.3:
                merged[-1] = [min(prev[0],b[0]), min(prev[1],b[1]),
                               max(prev[2],b[2]), max(prev[3],b[3])]
            else:
                merged.append(b)
        boxes = merged

    n_orig = len(orig_text)
    n_new  = len(new_text)

    # 字数不够：均分兜底
    if len(boxes) < 2:
        _fallback = render_stretched_text(new_text, font_path, bx2-bx1, by2-by1, fill, bg_color)
        img.paste(_fallback, (bx1, by1))
        return

    # ── 2. 估算字号和 scale_y（基于第一个字） ──
    ref_box = boxes[0]
    ref_char = orig_text[0] if orig_text else "国"
    ref_w = max(ref_box[2] - ref_box[0], 1)
    ref_h = max(ref_box[3] - ref_box[1], 1)

    # 二分找到渲染宽度 ≈ ref_w 的字号
    lo, hi = 4, 300
    while lo < hi:
        mid = (lo + hi + 1) // 2
        try:
            fnt = _IF.truetype(font_path, mid)
            tmp_img = _Img.new("RGB", (mid*3, mid*3), (255,255,255))
            tmp_d = _Draw.Draw(tmp_img)
            bb = tmp_d.textbbox((0,0), ref_char, font=fnt)
            rw = bb[2] - bb[0]
        except Exception:
            break
        if rw <= ref_w:
            lo = mid
        else:
            hi = mid - 1
    original_size = lo

    try:
        fnt_ref = _IF.truetype(font_path, original_size)
        tmp_img = _Img.new("RGB", (original_size*3, original_size*3), (255,255,255))
        tmp_d = _Draw.Draw(tmp_img)
        bb = tmp_d.textbbox((0,0), ref_char, font=fnt_ref)
        H_original = max(bb[3] - bb[1], 1)
    except Exception:
        H_original = original_size
    scale_y = ref_h / H_original

    # ── 3. 计算每个新字的放置位置 ──
    if n_new == n_orig and len(boxes) >= n_orig:
        # 字数一致：直接用原字 bbox 的左边缘
        char_boxes = boxes[:n_orig]
    else:
        # 字数不一致：固定总宽度均分
        total_x1 = boxes[0][0]
        total_x2 = boxes[-1][2]
        total_w  = total_x2 - total_x1
        char_w   = total_w / max(n_new, 1)
        char_y1  = min(b[1] for b in boxes)
        char_y2  = max(b[3] for b in boxes)
        char_boxes = [
            [int(total_x1 + i * char_w), char_y1,
             int(total_x1 + (i+1) * char_w), char_y2]
            for i in range(n_new)
        ]

    # ── 4. 逐字渲染并贴回 ──
    try:
        fnt = _IF.truetype(font_path, original_size)
    except Exception:
        fnt = _IF.load_default()

    for i, ch in enumerate(new_text):
        if i >= len(char_boxes):
            break
        cb = char_boxes[i]
        cx1, cy1, cx2, cy2 = cb[0], cb[1], cb[2], cb[3]

        # 渲染单字
        tmp = _Img.new("RGB", (original_size*3, original_size*3), bg_color)
        d   = _Draw.Draw(tmp)
        bb  = d.textbbox((0,0), ch, font=fnt)
        cw  = max(bb[2]-bb[0], 1)
        ch_ = max(bb[3]-bb[1], 1)
        rendered = _Img.new("RGB", (cw, ch_), bg_color)
        dr = _Draw.Draw(rendered)
        dr.text((-bb[0], -bb[1]), ch, font=fnt, fill=fill)

        # 纵向拉伸
        new_h = max(int(ch_ * scale_y), 1)
        stretched = rendered.resize((cw, new_h), _Img.LANCZOS)

        # 水平居中于原字 bbox 内
        paste_x = cx1 + max((cx2 - cx1 - cw) // 2, 0)
        paste_y = cy1

        # 先擦除该字区域
        draw_main = _Draw.Draw(img)
        draw_main.rectangle([cx1-2, cy1-2, cx2+2, cy2+2], fill=bg_color)

        img.paste(stretched, (paste_x, paste_y))


def render_stretched_text(text: str, font_path: str,
                           target_w: int, target_h: int,
                           fill: tuple, bg_color: tuple) -> "Image.Image":
    """
    渲染文字并纵向拉伸，使结果尺寸精确为 (target_w, target_h)。

    流程：
      1. 用 fit_font_to_width 找到使渲染宽度 ≈ target_w 的字号
      2. 渲染到白底 patch（原始高度 H_orig）
      3. PIL resize 到 (target_w, target_h)，保持水平尺寸不变，垂直拉伸
      4. 返回 RGB Image

    这样新文字的宽度和高度（含拉伸）都与原图一致。
    """
    from PIL import Image as _Img, ImageDraw as _Draw

    font_obj, rw, rh = fit_font_to_width(font_path, text, target_w, mode="red")

    # 渲染到宽裕的画布，避免文字被截断
    canvas_w = max(rw + 20, target_w + 20)
    canvas_h = max(rh + 20, 60)
    patch = _Img.new("RGB", (canvas_w, canvas_h), bg_color)
    drw = _Draw.Draw(patch)
    bb = drw.textbbox((0, 0), text, font=font_obj)
    rendered_w = bb[2] - bb[0]
    rendered_h = bb[3] - bb[1]
    # 水平居中，垂直居中
    tx = max((canvas_w - rendered_w) // 2 - bb[0], 0)
    ty = max((canvas_h - rendered_h) // 2 - bb[1], 0)
    drw.text((tx, ty), text, font=font_obj, fill=fill)

    # 裁剪到有效文字区域，再 resize 到目标尺寸（含纵向拉伸）
    patch_crop = patch.crop((0, 0, canvas_w, canvas_h))
    result = patch_crop.resize((target_w, target_h), _Img.LANCZOS)
    return result


def measure_ink_bbox(img: "Image.Image", x1: int, y1: int, x2: int, y2: int,
                     W: int, H: int, is_red: bool = False, expand: int = 0) -> tuple:
    """
    在图像区域内通过像素投影找到实际有墨水的 y 范围（含安全边距）。
    expand：搜索区域向上下各扩展 expand 像素。
    返回 (ink_y1, ink_y2)；找不到则退回传入的 bbox。
    """
    import numpy as np
    cx1 = max(x1, 0);          cx2 = min(x2, W)
    cy1 = max(y1 - expand, 0); cy2 = min(y2 + expand, H)
    if cx2 <= cx1 or cy2 <= cy1:
        return y1, y2
    arr = np.array(img)
    region = arr[cy1:cy2, cx1:cx2]
    if is_red:
        mask = (region[:, :, 0] > 150) & (region[:, :, 1] < 80) & (region[:, :, 2] < 80)
    else:
        mask = (region[:, :, 0] < 150) & (region[:, :, 1] < 150) & (region[:, :, 2] < 150)
    rows = np.any(mask, axis=1)
    if not np.any(rows):
        return y1, y2
    ink_rows = np.where(rows)[0]
    return max(cy1 + int(ink_rows[0]) - 2, 0), min(cy1 + int(ink_rows[-1]) + 2, H - 1)


def measure_char_ink_rect(img: "Image.Image", x1: int, y1: int, x2: int, y2: int,
                          W: int, H: int, is_red: bool = False) -> tuple:
    """
    对单个字符的大致 bbox，通过像素水平+垂直双向投影精确定位其墨水矩形。
    搜索范围向上下左右各扩展半个字符宽/高，覆盖笔画溢出。
    返回 (rx1, ry1, rx2, ry2)：实际墨水像素的精确矩形（含 2px 边距）。
    若找不到墨水像素，退回传入的 bbox。
    """
    import numpy as np
    char_w = max(x2 - x1, 1)
    char_h = max(y2 - y1, 1)
    # 搜索范围扩展半个字符尺寸，确保能找到溢出笔画
    sx1 = max(x1 - char_w // 2, 0)
    sx2 = min(x2 + char_w // 2, W)
    sy1 = max(y1 - char_h // 2, 0)
    sy2 = min(y2 + char_h // 2, H)

    arr = np.array(img)
    region = arr[sy1:sy2, sx1:sx2]
    if is_red:
        mask = (region[:, :, 0] > 150) & (region[:, :, 1] < 80) & (region[:, :, 2] < 80)
    else:
        mask = (region[:, :, 0] < 150) & (region[:, :, 1] < 150) & (region[:, :, 2] < 150)

    if not np.any(mask):
        return x1, y1, x2, y2

    # 垂直投影：找有墨水的行范围（y）
    rows_has_ink = np.any(mask, axis=1)
    ink_rows = np.where(rows_has_ink)[0]
    ry1 = max(sy1 + int(ink_rows[0])  - 2, 0)
    ry2 = min(sy1 + int(ink_rows[-1]) + 2, H - 1)

    # 水平投影：找有墨水的列范围（x）
    cols_has_ink = np.any(mask, axis=0)
    ink_cols = np.where(cols_has_ink)[0]
    rx1 = max(sx1 + int(ink_cols[0])  - 2, 0)
    rx2 = min(sx1 + int(ink_cols[-1]) + 2, W - 1)

    return rx1, ry1, rx2, ry2


def draw_text_centered(draw, x1: int, y1: int, x2: int, y2: int,
                       text: str, font, fill: tuple):
    """
    在 (x1,y1)-(x2,y2) 的格子内水平左对齐、垂直居中写入 text。
    用 textbbox 补偿 PIL ascender 偏移，解决"贴歪"问题。
    """
    # 量出实际渲染 bbox（相对于 draw.text 的起点 (0,0)）
    bb = draw.textbbox((0, 0), text, font=font)
    # bb[1] 通常为负（ascender 延伸到基线以上），bb[3]-bb[1] 是可见高度
    rendered_h = bb[3] - bb[1]
    cell_h = y2 - y1
    # 垂直居中：补偿 ascender 偏移
    ty = y1 + (cell_h - rendered_h) // 2 - bb[1]
    # 水平：左对齐，补偿水平偏移
    tx = x1 - bb[0]
    draw.text((tx, ty), text, font=font, fill=fill)


def estimate_blur_sigma(img: "Image.Image", x1: int, y1: int, x2: int, y2: int,
                        is_red: bool = False) -> float:
    """
    估算原图文字区域的模糊程度（高斯 σ）。
    原理：对文字边缘做梯度分析，边缘过渡带越宽说明越模糊。
    返回建议对新渲染文字施加的高斯 blur radius（0.0 表示不模糊）。
    """
    try:
        import numpy as np
        import cv2
        W_, H_ = img.size
        rx1 = max(x1, 0); ry1 = max(y1, 0)
        rx2 = min(x2, W_); ry2 = min(y2, H_)
        if rx2 <= rx1 or ry2 <= ry1:
            return 0.5
        arr = np.array(img)[ry1:ry2, rx1:rx2]
        if is_red:
            # 提取红色通道，文字像素亮
            gray = arr[:, :, 0].astype(np.float32)
        else:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
        # Sobel 梯度幅值，衡量边缘锐利度
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.sqrt(gx**2 + gy**2)
        # 取梯度幅值 > 阈值的边缘像素，计算过渡带宽度代理
        edge_mask = mag > (mag.max() * 0.2)
        if not np.any(edge_mask):
            return 0.5
        # 锐利文字梯度高且集中，模糊文字梯度低且分散
        # 用"最大梯度 / 平均梯度"的倒数估算扩散程度
        ratio = float(np.mean(mag[edge_mask])) / (float(mag.max()) + 1e-6)
        # ratio 接近 1 → 锐利(sigma≈0)；接近 0.2 → 较糊(sigma≈1.0)
        sigma = max(0.0, min(1.2, (0.6 - ratio) * 2.5 + 0.3))
        return round(sigma, 2)
    except Exception:
        return 0.5  # 默认轻微模糊


def _apply_blur_to_patch(patch: "Image.Image", sigma: float) -> "Image.Image":
    """对渲染补丁应用高斯模糊，sigma<=0.15 时跳过（视觉无差异）。"""
    if sigma <= 0.15:
        return patch
    from PIL import ImageFilter
    return patch.filter(ImageFilter.GaussianBlur(radius=sigma))


def _paste_text_centered(img: "Image.Image",
                         x1: int, y1: int, x2: int, y2: int,
                         text: str, font, fill: tuple,
                         bg_color: tuple = None,
                         blur_sigma: float = 0.0) -> None:
    """
    在背景色补丁上居中写字，应用模糊后贴回原图。
    blur_sigma：高斯模糊半径，0 表示不模糊；由调用方传入估算值。
    """
    W, H = img.size
    if bg_color is None:
        bg_color = _sample_bg(img, x1, y1, x2, y2, W, H)
    pw = max(x2 - x1, 1)
    ph = max(y2 - y1, 1)
    patch = Image.new("RGB", (pw, ph), bg_color)
    pd = ImageDraw.Draw(patch)
    bb = pd.textbbox((0, 0), text, font=font)
    rendered_h = bb[3] - bb[1]
    ty = (ph - rendered_h) // 2 - bb[1]
    tx = max(-bb[0], 0)
    pd.text((tx, ty), text, font=font, fill=fill)
    patch = _apply_blur_to_patch(patch, blur_sigma)
    img.paste(patch, (x1, y1))


def sample_red_color(img: "Image.Image", bx1: int, by1: int, bx2: int, by2: int) -> tuple:
    """
    从行 bbox 内采样红色像素的中位颜色，用于红头文字写入。
    如果采不到红色像素（已被覆盖等），返回默认印刷红色。
    """
    import numpy as np
    arr = np.array(img)
    H, W = arr.shape[:2]
    y1 = max(by1, 0); y2 = min(by2, H)
    x1 = max(bx1, 0); x2 = min(bx2, W)
    region = arr[y1:y2, x1:x2]
    # 红色像素：R>150, G<80, B<80
    mask = (region[:,:,0] > 150) & (region[:,:,1] < 80) & (region[:,:,2] < 80)
    reds = region[mask]
    if len(reds) >= 5:
        return tuple(int(np.median(reds[:, i])) for i in range(3))
    return (220, 10, 10)  # 印刷红兜底


def get_paddle_lines(image_path: str) -> list:
    """
    用 RapidOCR 识别图片，返回行级列表。每行结构：
    {
        "text": "识别文本",
        "x1": px, "y1": px, "x2": px, "y2": px,  # 行 bbox
        "chars": [{"char":"字","x1":px,"y1":px,"x2":px,"y2":px}, ...]
    }
    chars 按行 bbox 等分（RapidOCR 不提供字级坐标，用行宽/字数均分）。
    """
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()
    pil_img = Image.open(image_path).convert("RGB")
    result, _ = engine(np.array(pil_img))
    lines = []
    if not result:
        return lines
    for item in result:
        if len(item) < 2:
            continue
        pts = item[0]
        text = str(item[1]) if len(item) > 1 else ""
        if not text:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bx1, by1, bx2, by2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        n = len(text)
        char_w = (bx2 - bx1) / n if n > 0 else (bx2 - bx1)
        chars = []
        for i, ch in enumerate(text):
            chars.append({
                "char": ch,
                "x1": int(bx1 + i * char_w),
                "y1": int(by1),
                "x2": int(bx1 + (i + 1) * char_w),
                "y2": int(by2),
            })
        lines.append({
            "text": text,
            "x1": bx1, "y1": by1,
            "x2": bx2, "y2": by2,
            "chars": chars,
        })
    return lines


def _text_similarity(a: str, b: str) -> float:
    """简单字符重叠率，用于文本匹配。忽略空格。"""
    a = a.replace(" ", "")
    b = b.replace(" ", "")
    if not a or not b:
        return 0.0
    # 以较短串的字符为基准，计算在较长串中出现的比例
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    matched = sum(1 for ch in shorter if ch in longer)
    return matched / len(shorter)


def match_style_by_text(paddle_lines: list, qwen_blocks: list) -> list:
    """
    把 Qwen-VL 的字体/颜色/加粗信息，通过文本内容匹配附加到 PaddleOCR 行上。
    返回增强后的 paddle_lines，每行新增 font/bold/color 字段。

    匹配策略：对每个 PaddleOCR 行，找文本相似度最高的 Qwen-VL 块，
    相似度超过 0.4 则采用其样式，否则用位置推断兜底。
    """
    for pl in paddle_lines:
        pt = pl["text"].replace(" ", "")
        best_score = 0.0
        best_block = None
        for qb in qwen_blocks:
            score = _text_similarity(pt, qb.get("text", ""))
            if score > best_score:
                best_score = score
                best_block = qb
        if best_block and best_score >= 0.4:
            pl["font"]   = best_block.get("font", "")
            pl["bold"]   = best_block.get("bold", False)
            pl["color"]  = best_block.get("color", "black")
            pl["region"] = best_block.get("region", "body")
        else:
            pl["font"]   = ""
            pl["bold"]   = False
            pl["color"]  = ""   # 空字符串，localize_image 里用位置推断填充
            pl["region"] = ""
    return paddle_lines


def detect_font_blocks(image_bytes: bytes, mime: str, config: dict,
                       img_w: int = 0, img_h: int = 0) -> list:
    """Qwen-VL识别图片中所有文字块的字体/位置信息（像素bbox）。"""
    prompt = FONT_DETECT_PROMPT.format(img_w=img_w, img_h=img_h)
    raw = call_vision(image_bytes, mime, prompt, config)
    print(f"  Qwen-VL输出（前400字）: {raw[:400]}")
    match = re.search(r'\[[\s\S]*\]', raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return []


def extract_ocr_text_from_blocks(blocks: list) -> str:
    """从字体块列表中拼接出全文，用于生成替换词表。"""
    return "\n".join(b.get("text", "") for b in blocks if b.get("text"))


def get_font_path(font_name: str, font_dir: Path = None) -> str:
    """解析字体名称到文件路径，优先使用提取/指定的字体目录。"""
    if font_dir:
        for suffix in (".ttf", ".otf", ".ttc"):
            candidate = font_dir / f"{font_name}{suffix}"
            if candidate.exists():
                return str(candidate)
            # 模糊匹配：字体名包含在文件名中
            for f in font_dir.glob(f"*{font_name.split('_')[0]}*{suffix}"):
                return str(f)
    path = FONT_MAP.get(font_name)
    if path and os.path.exists(path):
        return path
    # 回退：仿宋
    return FONT_MAP["仿宋_GB2312"]


# ─────────────────────────────────────────────
# 新流程：区域分类 / 掩码提取 / 字符定位 / 字体匹配 / 逐字渲染
# ─────────────────────────────────────────────

def classify_line_region(line: dict, blocks: list, H: int) -> str:
    """
    在 Qwen-VL 5类基础上，通过 regex 细分 heading1/2/3。
    red_header / doc_number / imprint 直接沿用 Qwen-VL 结果。
    body 和 title 中的标题格式行：
      ^[一二三四五六七八九十]+[、．.] → heading1
      ^[（(][一二三四五六七八九十]+[）)] → heading2
      ^\d+[\.、]                        → heading3
    """
    region = (line.get("region") or "").strip()
    if region in ("red_header", "doc_number", "imprint"):
        return region
    text = (line.get("text") or "").strip()
    if re.search(r'^[一二三四五六七八九十]+[、．.]', text):
        return "heading1"
    if re.search(r'^[（(][一二三四五六七八九十]+[）)]', text):
        return "heading2"
    if re.search(r'^\d+[\.、]', text):
        return "heading3"
    return region if region else "body"


def extract_text_mask(img_arr, x1: int, y1: int, x2: int, y2: int,
                      is_red: bool = False):
    """
    从图像区域提取文字像素掩码。
    is_red=True：用 HSV 范围提取红色笔画；否则用 Otsu 提取暗像素。
    返回 uint8 二值图（255=文字，0=背景），尺寸与裁剪区域相同。
    """
    import cv2
    import numpy as np
    region = img_arr[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
    if region.size == 0:
        return np.zeros((max(y2 - y1, 1), max(x2 - x1, 1)), dtype=np.uint8)

    if is_red:
        hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
        # 红色在 HSV 中有两段色相范围
        lo1 = np.array([0,   80, 80],  dtype=np.uint8)
        hi1 = np.array([12, 255, 255], dtype=np.uint8)
        lo2 = np.array([155, 80, 80],  dtype=np.uint8)
        hi2 = np.array([180, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lo1, hi1) | cv2.inRange(hsv, lo2, hi2)
    else:
        gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY)
        _, mask = cv2.threshold(gray, 0, 255,
                                cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 形态学闭运算，连接断笔（核3×3）
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def _fallback_uniform(x1: int, y1: int, x2: int, y2: int, n: int) -> list:
    """均分行宽为 n 个等宽格子，返回 [(cx1,cy1,cx2,cy2), ...]。"""
    if n <= 0:
        return []
    step = (x2 - x1) / n
    return [(int(x1 + i * step), y1, int(x1 + (i + 1) * step), y2)
            for i in range(n)]


def locate_chars_by_mask(mask, x1: int, y1: int, char_count: int) -> list:
    """
    用 findContours 从掩码中定位每个字符的 bbox，返回长度 == char_count 的列表。
    每项：(box_x1, box_y1, box_x2, box_y2) 为原图坐标。

    流程：
      1. 找轮廓，过滤噪点（area < 总面积*0.5%）
      2. 按 x 排序
      3. 合并相邻轮廓（x 间距 < 平均字宽*30%）→ 合并后每项对应一个字符候选
      4. 若结果数与 char_count 相差超50%，退化为均分
      5. 否则补充/合并到精确 char_count 个
    """
    import cv2
    import numpy as np

    if not CV2_OK or mask is None or mask.size == 0:
        return _fallback_uniform(x1, y1, x1 + max(mask.shape[1] if mask is not None else 1, 1),
                                 y1 + max(mask.shape[0] if mask is not None else 1, 1),
                                 char_count)

    h_mask, w_mask = mask.shape[:2]
    total_area = h_mask * w_mask
    min_area = total_area * 0.005

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for cnt in contours:
        if cv2.contourArea(cnt) < min_area:
            continue
        cx, cy, cw, ch = cv2.boundingRect(cnt)
        # 转换到原图坐标
        boxes.append((x1 + cx, y1 + cy, x1 + cx + cw, y1 + cy + ch))

    if not boxes:
        return _fallback_uniform(x1, y1, x1 + w_mask, y1 + h_mask, char_count)

    # 按 x 排序
    boxes.sort(key=lambda b: b[0])

    # 估算平均字宽（用于合并阈值）
    avg_char_w = (boxes[-1][2] - boxes[0][0]) / max(char_count, 1)
    merge_gap = avg_char_w * 0.3

    # 合并 x 轴相邻轮廓
    merged = [list(boxes[0])]
    for b in boxes[1:]:
        prev = merged[-1]
        gap = b[0] - prev[2]  # 当前左边 - 前一个右边
        if gap < merge_gap:
            # 扩展前一个框
            prev[2] = max(prev[2], b[2])
            prev[3] = max(prev[3], b[3])
            prev[1] = min(prev[1], b[1])
        else:
            merged.append(list(b))

    n_found = len(merged)

    # 偏差超50%：退化为均分
    if char_count > 0 and abs(n_found - char_count) / char_count > 0.5:
        return _fallback_uniform(x1, y1, x1 + w_mask, y1 + h_mask, char_count)

    # 调整到精确 char_count 个
    if n_found < char_count:
        # 补均分
        fallback = _fallback_uniform(x1, y1, x1 + w_mask, y1 + h_mask, char_count)
        merged.extend(fallback[n_found:])
    elif n_found > char_count:
        # 合并多余（依次合并相邻最小间距的两个）
        while len(merged) > char_count:
            # 找间距最小的相邻对
            min_gap = float('inf')
            min_i = 0
            for i in range(len(merged) - 1):
                gap = merged[i + 1][0] - merged[i][2]
                if gap < min_gap:
                    min_gap = gap
                    min_i = i
            # 合并 min_i 和 min_i+1
            a, b_ = merged[min_i], merged[min_i + 1]
            merged[min_i] = [min(a[0], b_[0]), min(a[1], b_[1]),
                              max(a[2], b_[2]), max(a[3], b_[3])]
            merged.pop(min_i + 1)

    return [tuple(b) for b in merged[:char_count]]


def match_font_by_render(img_arr, x1: int, y1: int, x2: int, y2: int,
                         text: str, candidate_fonts: list,
                         target_h: int, is_red: bool = False) -> tuple:
    """
    渲染候选字体，与原图区域二值对比（SSIM 或 IoU），返回 (best_font_path, best_score)。
    结果不在此处缓存——调用方在 font_match_cache 中管理缓存。
    """
    import cv2
    import numpy as np
    from PIL import Image as _Img, ImageDraw as _Draw

    if not CV2_OK or not text or not candidate_fonts:
        return (candidate_fonts[0] if candidate_fonts else FONT_MAP["仿宋_GB2312"]), 0.0

    # 裁剪原图区域并二值化
    crop_rgb = img_arr[max(y1, 0):max(y2, 0), max(x1, 0):max(x2, 0)]
    if crop_rgb.size == 0:
        return candidate_fonts[0], 0.0
    crop_h, crop_w = crop_rgb.shape[:2]

    crop_gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    _, crop_bin = cv2.threshold(crop_gray, 0, 255,
                                cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    def _font_has_cjk(fp: str, sample_text: str) -> bool:
        """检查字体文件是否包含 sample_text 中的汉字字形（而非用 .notdef 方块替代）。"""
        try:
            from fontTools.ttLib import TTFont as _TTFont
            tt = _TTFont(fp, fontNumber=0)
            cmap = tt.getBestCmap()
            if cmap is None:
                return False
            for ch in sample_text:
                cp = ord(ch)
                if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
                    if cp not in cmap:
                        return False
            return True
        except Exception:
            # fontTools 不可用或解析失败 → 用渲染宽度粗判
            try:
                from PIL import ImageFont as _IF
                fnt = _IF.truetype(fp, 20)
                w0 = fnt.getlength("国")
                w1 = fnt.getlength("A")
                # 汉字宽度应明显大于 ASCII 字母，否则视为无汉字字形
                return w0 > w1 * 1.2
            except Exception:
                return True  # 无法判断时不过滤

    # 默认 fallback：从候选中找第一个存在且含汉字的字体
    _fallback_fp = FONT_MAP.get("仿宋_GB2312", candidate_fonts[0])
    best_path = next(
        (f for f in candidate_fonts if os.path.exists(f) and _font_has_cjk(f, text)),
        _fallback_fp
    )
    best_score = -1.0

    for font_path in candidate_fonts:
        if not os.path.exists(font_path):
            continue
        # 跳过不含汉字字形的字体（避免方块误判高分）
        if not _font_has_cjk(font_path, text):
            continue
        try:
            # 在白底 patch 上渲染文字
            mode_str = "red" if is_red else "title"
            font_obj = fit_font_to_height(font_path, target_h, mode=mode_str)
            fill = (220, 10, 10) if is_red else (0, 0, 0)

            patch = _Img.new("RGB", (crop_w * 2, max(crop_h, 1)), (255, 255, 255))
            drw = _Draw.Draw(patch)
            bb = drw.textbbox((0, 0), text, font=font_obj)
            ty = (crop_h - (bb[3] - bb[1])) // 2 - bb[1]
            drw.text((max(-bb[0], 0), ty), text, font=font_obj, fill=fill)

            # 缩放到 crop 尺寸
            patch_resized = patch.resize((crop_w, crop_h), _Img.LANCZOS)
            render_arr = np.array(patch_resized.convert("L"))
            _, render_bin = cv2.threshold(render_arr, 127, 255, cv2.THRESH_BINARY)

            # 计算相似度
            if SKIMAGE_OK:
                score = float(_ssim_fn(crop_bin, render_bin,
                                       data_range=255))
            else:
                # IoU 回退
                inter = np.logical_and(crop_bin > 127, render_bin > 127).sum()
                union = np.logical_or(crop_bin > 127, render_bin > 127).sum()
                score = float(inter / union) if union > 0 else 0.0

            if score > best_score:
                best_score = score
                best_path = font_path
        except Exception as e:
            print(f"    [字体匹配] {Path(font_path).name} 失败: {e}")
            continue

    return best_path, best_score


def render_replacement_chars(img: "Image.Image",
                              new_text: str,
                              char_positions: list,
                              line_y1: int, line_y2: int,
                              font, color: tuple,
                              bg_color: tuple,
                              blur_sigma: float = 0.0) -> None:
    """
    逐字符写入替换文字。
    - char_positions 长度 == len(new_text)：1:1 对应写入各字符位置
    - 否则：按首尾 x 范围均分写入
    blur_sigma：由调用方传入 estimate_blur_sigma 的估算值，模拟原图模糊程度。
    """
    n = len(new_text)
    if n == 0:
        return

    if len(char_positions) == n:
        for i, ch in enumerate(new_text):
            cx1, _, cx2, _ = char_positions[i]
            _paste_text_centered(img, cx1, line_y1, cx2, line_y2,
                                  ch, font, color, bg_color, blur_sigma)
    else:
        # 字符数不匹配，按总范围均分
        if not char_positions:
            return
        total_x1 = char_positions[0][0]
        total_x2 = char_positions[-1][2]
        step = (total_x2 - total_x1) / n
        for i, ch in enumerate(new_text):
            cx = int(total_x1 + i * step)
            _paste_text_centered(img, cx, line_y1, cx + max(int(step), 1), line_y2,
                                  ch, font, color, bg_color, blur_sigma)


def localize_image(
    image_path: Path,
    tgt_province: str,
    tgt_authority: str,
    config: dict,
    output_dir: Path,
    font_dir: Path = None,
    skip_vision: bool = False,
    explicit_mapping: dict = None,
) -> Path:
    """图片格式保留本地化主流程。"""
    if not PIL_OK:
        raise RuntimeError("图片处理需要 Pillow，请运行: pip install Pillow")
    tgt_province = shorten_province(tgt_province)

    # 每次调用清空字体匹配缓存（不同文件可能布局不同）
    font_match_cache.clear()

    suffix = image_path.suffix.lower()
    mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else "image/png"
    image_bytes = image_path.read_bytes()

    img = Image.open(image_path).convert("RGB")
    W, H = img.size

    # 检查是否有缓存的 blocks.json（--skip-vision 时直接复用）
    blocks_json = output_dir / f"{image_path.stem}_blocks.json"
    if skip_vision and blocks_json.exists():
        print(f"  [图片] Step1: 跳过Qwen-VL，复用缓存 {blocks_json.name}")
        blocks = json.loads(blocks_json.read_text(encoding="utf-8"))
    else:
        print("  [图片] Step1: Qwen-VL识别行级字体信息...")
        blocks = detect_font_blocks(image_bytes, mime, config, img_w=W, img_h=H)
        blocks_json.write_text(json.dumps(blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  识别到 {len(blocks)} 个行块")

    if not blocks:
        print("  未识别到文字块，跳过替换")
        return image_path

    print("  [图片] Step2: 生成替换词表...")
    raw_text = extract_ocr_text_from_blocks(blocks)
    mapping = build_replacement_mapping(raw_text, "", tgt_province, tgt_authority, config)
    mapping.update(explicit_mapping or {})
    print(f"  替换词表（{len(mapping)}条）:")
    for k, v in mapping.items():
        print(f"    [{k}] → [{v}]")

    if not mapping:
        print("  无需替换内容，跳过")
        return image_path

    # Step3: RapidOCR 行级识别 + 文本匹配获取样式 + 掩码字符定位
    print("  [图片] Step3: RapidOCR行级识别 + 字符掩码定位...")
    import numpy as _np
    img_arr = _np.array(img)

    try:
        rapid_lines = get_paddle_lines(str(image_path))
        print(f"  RapidOCR识别到 {len(rapid_lines)} 行")
        active_blocks = match_style_by_text(rapid_lines, blocks)
    except Exception as e:
        print(f"  [图片] Step3: RapidOCR失败({e})，回退到Qwen-VL行级替换")
        active_blocks = []
        for b in blocks:
            active_blocks.append(b)

    # 对每行：细分区域类型 + 掩码字符定位
    for bl in active_blocks:
        text = bl.get("text", "")
        if not text:
            bl["region_classified"] = bl.get("region", "body") or "body"
            bl["char_positions"] = []
            continue

        region_label = classify_line_region(bl, active_blocks, H)
        bl["region_classified"] = region_label

        bx1_ = bl.get("x1", 0); by1_ = bl.get("y1", 0)
        bx2_ = bl.get("x2", W); by2_ = bl.get("y2", H)
        is_red_ = (bl.get("color", "") == "red") or (by1_ / H < 0.15 if H > 0 else False)

        if CV2_OK:
            try:
                mask_ = extract_text_mask(img_arr, bx1_, by1_, bx2_, by2_, is_red_)
                char_positions_ = locate_chars_by_mask(mask_, bx1_, by1_, len(text))
            except Exception as em:
                print(f"    [掩码定位失败] {em}，均分")
                n_ = max(len(text), 1)
                cw_ = (bx2_ - bx1_) / n_
                char_positions_ = [(int(bx1_ + i * cw_), by1_,
                                     int(bx1_ + (i + 1) * cw_), by2_) for i in range(n_)]
        else:
            n_ = max(len(text), 1)
            cw_ = (bx2_ - bx1_) / n_
            char_positions_ = [(int(bx1_ + i * cw_), by1_,
                                 int(bx1_ + (i + 1) * cw_), by2_) for i in range(n_)]
        bl["char_positions"] = char_positions_

    print(f"  Step3完成，共{len(active_blocks)}行")


    print("  [图片] Step4: 逐行/逐字覆盖写入...")
    draw = ImageDraw.Draw(img)
    replaced_count = 0

    # ── 计算两套全局字号：正文（body）和印发信息（imprint）分开算 ──
    import statistics as _stats

    PUNCT = set('，。；：！？、""''【】《》〔〕（）…—·,.:;!?\'\"[]()<>')

    body_heights = []
    imprint_heights = []
    for _b in active_blocks:
        _color  = _b.get("color", "black") or "black"
        _is_red = (_color == "red")
        _bold   = _b.get("bold", False)
        _region = _b.get("region", "") or ""
        _is_title = (_b.get("font", "") == "黑体" or _bold) and not _is_red
        _h = _b.get("y2", 0) - _b.get("y1", 0)
        if _h <= 5:
            continue
        if _region == "imprint":
            imprint_heights.append(_h)
        elif not _is_red and not _is_title:
            body_heights.append(_h)

    median_body_h    = int(_stats.median(body_heights))    if body_heights    else 32
    median_imprint_h = int(_stats.median(imprint_heights)) if imprint_heights else median_body_h

    _default_font_path = FONT_MAP["仿宋_GB2312"]
    body_font_global    = fit_font_to_height(_default_font_path, median_body_h,    mode="body")
    imprint_font_global = fit_font_to_height(_default_font_path, median_imprint_h, mode="body")
    print(f"  全局正文字号: median_body_h={median_body_h}px  印发信息字号: median_imprint_h={median_imprint_h}px")
    body_font_cache    = {_default_font_path: body_font_global}
    imprint_font_cache = {_default_font_path: imprint_font_global}

    # ── 预处理：识别连续的标题行组 ──
    title_groups = []  # [(start_idx, end_idx), ...]
    i = 0
    while i < len(active_blocks):
        block = active_blocks[i]
        region = block.get("region_classified") or block.get("region", "") or ""
        font_name = block.get("font", "")
        bold = block.get("bold", False)
        color_hint = block.get("color", "black")
        by1 = block.get("y1")
        is_red = (color_hint == "red")
        is_title = (region == "title") or ((font_name == "黑体" or bold) and not is_red and region != "doc_number")

        if is_title:
            # 找到连续的标题行
            start_idx = i
            end_idx = i
            last_y2 = block.get("y2", 0)

            for j in range(i + 1, len(active_blocks)):
                next_block = active_blocks[j]
                next_region = next_block.get("region_classified") or next_block.get("region", "") or ""
                next_font = next_block.get("font", "")
                next_bold = next_block.get("bold", False)
                next_color = next_block.get("color", "black")
                next_y1 = next_block.get("y1")
                next_is_red = (next_color == "red")
                next_is_title = (next_region == "title") or ((next_font == "黑体" or next_bold) and not next_is_red and next_region != "doc_number")

                # 检查是否连续（y坐标间隔小于3倍字高）
                if next_is_title and next_y1 and last_y2 and (next_y1 - last_y2 < median_body_h * 3):
                    end_idx = j
                    last_y2 = next_block.get("y2", last_y2)
                else:
                    break

            title_groups.append((start_idx, end_idx))
            i = end_idx + 1
        else:
            i += 1

    # 标记哪些行属于标题组
    title_group_map = {}  # {block_idx: group_id}
    for group_id, (start_idx, end_idx) in enumerate(title_groups):
        for idx in range(start_idx, end_idx + 1):
            title_group_map[idx] = group_id

    for block in active_blocks:
        orig_text = block.get("text", "").strip()
        if not orig_text:
            continue

        bx1 = block.get("x1")
        by1 = block.get("y1")
        bx2 = block.get("x2")
        by2 = block.get("y2")
        has_bbox = all(v is not None for v in [bx1, by1, bx2, by2])
        if not has_bbox:
            continue

        box_h    = by2 - by1
        font_name  = block.get("font", "")
        bold       = block.get("bold", False)
        region     = block.get("region_classified") or block.get("region", "") or ""
        color_hint = block.get("color", "black")
        if not color_hint:
            color_hint = "red" if (by1 / H < 0.15) else "black"
        is_red    = (color_hint == "red")
        is_title  = (region in RENDER_MATCH_REGIONS and not is_red) or \
                    ((font_name == "黑体" or bold) and not is_red)
        is_imprint = (region == "imprint")

        if not font_name:
            font_name = infer_font_by_position(by1 / H, bold)
        font_path = get_font_path(font_name, font_dir)

        if is_red:
            color = sample_red_color(img, bx1, by1, bx2, by2)
        else:
            color = (0, 0, 0)

        # 检查本行是否含有替换词
        modified_text = orig_text
        line_has_change = False
        for orig_word, new_word in mapping.items():
            if orig_word in modified_text:
                modified_text = modified_text.replace(orig_word, new_word)
                line_has_change = True

        # 发文文号：统一将各种括号转换为方头括号
        if region == "doc_number":
            # 替换所有可能的括号变体
            modified_text = modified_text.replace('（', '〔').replace('）', '〕')  # 中文圆括号
            modified_text = modified_text.replace('(', '〔').replace(')', '〕')    # 英文圆括号
            modified_text = modified_text.replace('[', '〔').replace(']', '〕')    # 方括号
            modified_text = modified_text.replace('【', '〔').replace('】', '〕')  # 中文方括号
            modified_text = modified_text.replace('{', '〔').replace('}', '〕')    # 花括号

        # 红头：无条件整行处理
        # 文号/标题/正文：有替换词时整行处理
        # 印发：有替换词时处理
        should_rewrite_whole_line = is_red or (line_has_change and region in ("doc_number", "title", "body")) or (is_title and line_has_change)

        if not line_has_change and not is_red:
            continue

        # ── 选字号：用像素投影测实际墨水高度，比 Qwen-VL bbox 更准 ──
        ink_y1, ink_y2 = measure_ink_bbox(img, bx1, by1, bx2, by2, W, H, is_red=is_red, expand=4)
        ink_h = max(ink_y2 - ink_y1, 1)
        effective_h = max(ink_h, int(box_h * 0.4))
        print(f"    [字号] box_h={box_h} ink_h={ink_h} effective_h={effective_h} region={region}")

        # ── 字体选择：优先用 Qwen-VL 识别的 font 字段，文件不存在时才走渲染匹配 ──
        qwen_font_path = get_font_path(font_name, font_dir) if font_name else None
        qwen_font_valid = qwen_font_path and os.path.exists(qwen_font_path)

        if region in RENDER_MATCH_REGIONS and CV2_OK:
            if qwen_font_valid:
                # Qwen-VL 给了字体且文件存在，直接用，跳过渲染匹配
                font_path = qwen_font_path
                print(f"    [字体] {region}: Qwen-VL指定 {Path(font_path).name}")
            else:
                # Qwen-VL 没给字体或文件找不到，走渲染匹配兜底
                if region not in font_match_cache:
                    avail_fonts = [f for f in CANDIDATE_FONTS if os.path.exists(f)]
                    if avail_fonts:
                        best_fp, score = match_font_by_render(
                            img_arr, bx1, by1, bx2, by2,
                            orig_text, avail_fonts, effective_h, is_red
                        )
                        font_match_cache[region] = (best_fp, score)
                        print(f"    [字体匹配] {region}: {Path(best_fp).name} score={score:.3f}")
                    else:
                        font_match_cache[region] = (font_path, 0.0)
                font_path = font_match_cache[region][0]
            pil_font = fit_font_to_height(font_path, effective_h,
                                          mode="red" if is_red else "title")
        elif is_red:
            pil_font = fit_font_to_height(font_path, effective_h, mode="red")
        elif is_title or region in ("doc_number",):
            pil_font = fit_font_to_height(font_path, effective_h, mode="title")
        elif is_imprint:
            if font_path not in imprint_font_cache:
                imprint_font_cache[font_path] = fit_font_to_height(
                    font_path, median_imprint_h, mode="body")
            pil_font = imprint_font_cache[font_path]
        else:
            if font_path not in body_font_cache:
                body_font_cache[font_path] = fit_font_to_height(
                    font_path, median_body_h, mode="body")
            pil_font = body_font_cache[font_path]

        bg_color = _sample_bg(img, bx1, by1, bx2, by2, W, H)
        # 估算该行原文字的模糊程度，用于新文字柔化
        line_blur_sigma = estimate_blur_sigma(img, bx1, by1, bx2, by2, is_red=is_red)

        if should_rewrite_whole_line:
            # ── 整行作为一个补丁 ──
            # 1. 擦除整行原始文字
            _inpaint_erase(img, bx1- 4, by1, bx2 + 4, by2, W, H, is_red, expand=0, bg_color=bg_color)
            # 2. 创建与行等大的补丁，在补丁上写完整文字，整块贴回
            pw = max(bx2 - bx1, 1)
            ph = max(by2 - by1, 1)
            if is_red:
                # 红头：逐字定位原字间距，逐字渲染贴回，不做水平缩放
                render_red_header_per_char(
                    img, img_arr,
                    orig_text, modified_text,
                    font_path,
                    bx1, by1, bx2, by2,
                    fill=color, bg_color=bg_color,
                )
                print(f"    逐字贴回(红头): [{orig_text[:20]}] → [{modified_text[:20]}]")
            else:
                # 发文文号：右边扩展3个字的宽度
                if region == "doc_number":
                    char_width_estimate = effective_h  # 估算一个字的宽度约等于字高
                    right_expand = char_width_estimate * 3
                    bx2_expanded = min(bx2 + right_expand, W)
                else:
                    bx2_expanded = bx2

                _inpaint_erase(img, bx1 - 4, by1, bx2_expanded + 4, by2, W, H, is_red, bg_color=bg_color)
                pw = max(bx2_expanded - bx1, 1); ph = max(by2 - by1, 1)
                patch = Image.new("RGB", (pw, ph), bg_color)
                pd = ImageDraw.Draw(patch)
                bb = pd.textbbox((0, 0), modified_text, font=pil_font)
                rendered_h = bb[3] - bb[1]
                ty = (ph - rendered_h) // 2 - bb[1]
                tx = max(-bb[0], 0)
                pd.text((tx, ty), modified_text, font=pil_font, fill=color)
                patch = _apply_blur_to_patch(patch, line_blur_sigma)
                img.paste(patch, (bx1, by1))
                print(f"    整行补丁: [{orig_text[:20]}] → [{modified_text[:20]}] region={region}")
            replaced_count += 1

        else:
            # ── 正文/印发行：只替换含替换词的局部区域 ──
            # 使用 Step3 中由掩码+轮廓定位的字符坐标
            char_positions_all = block.get("char_positions", [])
            char_text = orig_text  # 行全文，用于搜索替换词位置

            if not char_positions_all:
                # 无字符坐标，回退整行补丁
                _inpaint_erase(img, bx1 - 4, by1, bx2 + 4, by2, W, H, is_red, expand=0, bg_color=bg_color)
                pw = max(bx2 - bx1, 1); ph = max(by2 - by1, 1)
                patch = Image.new("RGB", (pw, ph), bg_color)
                pd = ImageDraw.Draw(patch)
                bb = pd.textbbox((0, 0), modified_text, font=pil_font)
                ty = (ph - (bb[3] - bb[1])) // 2 - bb[1]
                pd.text((max(-bb[0], 0), ty), modified_text, font=pil_font, fill=color)
                patch = _apply_blur_to_patch(patch, line_blur_sigma)
                img.paste(patch, (bx1, by1))
                print(f"    行补丁(无字级): [{orig_text[:20]}] → [{modified_text[:20]}]")
                replaced_count += 1
                continue

            # 有字符坐标：按词替换
            for orig_word, new_word in mapping.items():
                start = 0
                while True:
                    idx = char_text.find(orig_word, start)
                    if idx < 0:
                        break
                    end_idx = idx + len(orig_word)

                    # 从 char_positions 取被替换词对应的位置范围
                    if idx >= len(char_positions_all):
                        start = idx + 1
                        continue
                    cp_start = char_positions_all[idx]
                    cp_end   = char_positions_all[min(end_idx - 1, len(char_positions_all) - 1)]
                    ax0 = cp_start[0]
                    ax1 = cp_end[2]
                    ay0 = min(cp[1] for cp in char_positions_all[idx:min(end_idx, len(char_positions_all))])
                    ay1 = max(cp[3] for cp in char_positions_all[idx:min(end_idx, len(char_positions_all))])

                    # 墨水高度驱动字号
                    w_ink_y1, w_ink_y2 = measure_ink_bbox(img, ax0, ay0, ax1, ay1, W, H, is_red=is_red, expand=2)
                    ref_h = max(w_ink_y2 - w_ink_y1, int((ay1 - ay0) * 0.4))

                    # 标点溢出保护
                    PUNCT = set('，。；：！？、\u201c\u201d\u2018\u2019【】《》〔〕（）…—·,.:;!?\'\"[]()<>')
                    left_expand = 0
                    right_expand = 0
                    if idx > 0 and char_text[idx - 1] in PUNCT:
                        left_expand = int(ref_h * 0.6)
                    if end_idx < len(char_text) and char_text[end_idx] in PUNCT:
                        right_expand = int(ref_h * 0.6)

                    word_bg = _sample_bg(img, ax0, ay0, ax1, ay1, W, H)
                    _inpaint_erase(img, ax0 - 2 - left_expand, ay0,
                                   ax1 + 2 + right_expand, ay1,
                                   W, H, is_red, bg_color=word_bg)

                    # 用 render_replacement_chars 逐字写入（传入 blur_sigma）
                    word_positions = [char_positions_all[i]
                                      for i in range(idx, min(end_idx, len(char_positions_all)))]
                    render_replacement_chars(img, new_word, word_positions,
                                             ay0, ay1, pil_font, color, word_bg,
                                             blur_sigma=line_blur_sigma)

                    print(f"    字级替换: [{orig_word}]→[{new_word}] x={ax0}-{ax1} σ={line_blur_sigma}"
                          + (f" 左扩{left_expand}" if left_expand else "")
                          + (f" 右扩{right_expand}" if right_expand else ""))
                    replaced_count += 1
                    char_text = char_text[:idx] + new_word + char_text[end_idx:]
                    start = idx + len(new_word)

    out_path = output_dir / f"{image_path.stem}_localized{image_path.suffix}"
    img.save(str(out_path))
    print(f"  ✓ 输出: {out_path} （替换{replaced_count}处）")
    return out_path


def _inpaint_erase(img: "Image.Image", x1: int, y1: int, x2: int, y2: int,
                   W: int, H: int, is_red: bool = False, expand: int = 2,
                   bg_color: tuple = None) -> None:
    """
    用背景色矩形覆盖原文字区域，完全不透明。
    bg_color 为 None 时从周边像素采样（自适应米白/浅灰扫描件背景）。
    expand：向上下各扩展 expand 像素，覆盖笔画溢出（默认2pt）。
    """
    if bg_color is None:
        bg_color = _sample_bg(img, x1, y1, x2, y2, W, H)
    draw = ImageDraw.Draw(img)
    rx1 = max(x1 - 2, 0)
    ry1 = max(y1 - expand, 0)
    rx2 = min(x2 + 2, W)
    ry2 = min(y2 + expand, H)
    draw.rectangle([rx1, ry1, rx2, ry2], fill=bg_color)


def _sample_bg(img: "Image.Image", x1: int, y1: int, x2: int, y2: int,
               W: int, H: int) -> tuple:
    """
    采样文字区域周边的背景色，返回中位数颜色。
    策略：优先取文字区域正上方的一个水平条带（高8px），
    其次取左侧空白条带，取所有采样点 RGB 各通道中位数。
    """
    import numpy as np
    samples = []

    # 1. 文字正上方取一条（避免采到别的文字，取足够宽的范围）
    strip_y1 = max(y1 - 10, 0)
    strip_y2 = max(y1 - 2, 0)
    if strip_y2 > strip_y1:
        for px in range(max(x1, 0), min(x2, W), max((x2 - x1) // 10, 1)):
            for py in range(strip_y1, strip_y2):
                px_ = min(px, W - 1)
                py_ = min(py, H - 1)
                c = img.getpixel((px_, py_))
                if isinstance(c, int):
                    c = (c, c, c)
                samples.append(c[:3])

    # 2. 文字左侧空白条带
    left_x = max(x1 - 15, 0)
    right_x = max(x1 - 2, 0)
    if right_x > left_x:
        mid_y = (y1 + y2) // 2
        for px in range(left_x, right_x):
            for py in range(max(mid_y - 4, 0), min(mid_y + 4, H)):
                c = img.getpixel((min(px, W - 1), py))
                if isinstance(c, int):
                    c = (c, c, c)
                samples.append(c[:3])

    if not samples:
        return (255, 255, 255)

    arr = np.array(samples)
    # 过滤掉明显是文字的深色像素（R<150 或 G<150 说明是文字/线条）
    mask = (arr[:, 0] > 150) & (arr[:, 1] > 150) & (arr[:, 2] > 150)
    if mask.sum() > 0:
        arr = arr[mask]
    med = tuple(int(np.median(arr[:, i])) for i in range(3))
    return med



# ─────────────────────────────────────────────
# PDF 流程
# ─────────────────────────────────────────────

def extract_pdf_embedded_fonts(doc, font_cache_dir: Path) -> dict:
    """
    从 PDF 中提取所有嵌入字体，保存到 font_cache_dir，返回 {字体基名: 文件路径}。
    """
    font_cache_dir.mkdir(parents=True, exist_ok=True)
    extracted = {}
    seen_xrefs = set()
    for page in doc:
        for font_tuple in page.get_fonts(full=True):
            xref, _, font_type, base_name, *_ = font_tuple
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                font_data = doc.extract_font(xref)
                if not font_data or not font_data[3]:  # font_data[3] = raw bytes
                    continue
                raw_bytes = font_data[3]
                ext = ".ttf" if font_type in ("TrueType", "Type0") else ".otf"
                fname = re.sub(r'[^A-Za-z0-9_\-]', '_', base_name) + ext
                fpath = font_cache_dir / fname
                fpath.write_bytes(raw_bytes)
                extracted[base_name] = str(fpath)
                print(f"    提取嵌入字体: {base_name} → {fname}")
            except Exception as e:
                print(f"    跳过字体 {base_name} (xref={xref}): {e}")
    return extracted


def resolve_pdf_font(font_name: str, embedded: dict, font_dir: Path = None) -> str:
    """按优先级查找字体文件：指定目录 > 系统回退。
    不使用嵌入子集字体——嵌入字体是子集化的，新词汉字可能不在子集里导致乱码。
    """
    # 1. 指定字体目录
    if font_dir:
        for suffix in (".ttf", ".otf", ".ttc"):
            p = font_dir / f"{font_name}{suffix}"
            if p.exists():
                return str(p)

    # 2. 系统字体映射
    path = FONT_MAP.get(font_name)
    if path and os.path.exists(path):
        return path

    # 3. 名称模糊匹配系统字体
    lower = font_name.lower()
    if "fangsong" in lower or "仿宋" in lower:
        return FONT_MAP["仿宋_GB2312"]
    if "hei" in lower or "黑体" in lower:
        return FONT_MAP["SimHei"]
    if "kai" in lower or "楷体" in lower:
        return FONT_MAP["KaiTi_GB2312"]
    if "fzxbs" in lower or "fzxbsk" in lower or "xiaobiao" in lower or "小标宋" in lower:
        return _FZXBS_GBK
    if "times" in lower and "bold" in lower:
        return FONT_MAP["TimesNewRomanPS-BoldMT"]
    if "times" in lower:
        return FONT_MAP["TimesNewRomanPSMT"]
    if "zhongsong" in lower or "中宋" in lower:
        return FONT_MAP["STZhongsong"]
    if "song" in lower or "宋体" in lower:
        return FONT_MAP["SimSun"]
    return FONT_MAP["仿宋_GB2312"]


def localize_pdf(
    pdf_path: Path,
    tgt_province: str,
    tgt_authority: str,
    config: dict,
    output_dir: Path,
    font_dir: Path = None,
    no_preview: bool = False,
    src_authorities: list = None,
    explicit_mapping: dict = None,
) -> Path:
    """PDF 格式保留本地化主流程。"""
    if not FITZ_OK:
        raise RuntimeError("PDF 处理需要 PyMuPDF，请运行: pip install pymupdf")
    tgt_province = shorten_province(tgt_province)

    doc = fitz.open(str(pdf_path))

    print("  [PDF] Step1: 提取文字层...")
    page_texts = []  # 每页的文本，保留原始分页
    for i, page in enumerate(doc):
        text = page.get_text().strip()
        page_texts.append(text)
    all_text_parts = [t for t in page_texts if t]
    raw_text = "\n".join(all_text_parts)

    # 文字层不足则用Qwen-VL OCR
    cjk_count = len(re.findall(r'[\u4e00-\u9fff]', raw_text))
    if cjk_count < 30:
        print("  文字层中文不足，调用Qwen-VL OCR...")
        page_texts = []
        for i, page in enumerate(doc):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(200/72, 200/72), alpha=False)
            png_bytes = pixmap.tobytes("png")
            text = call_vision(png_bytes, "image/png",
                               "请逐行转写这张政府公文图片中的全部文字，保持原顺序，只输出文字。",
                               config)
            page_texts.append(text)
            print(f"    第{i+1}页OCR完成")
        raw_text = "\n".join(t for t in page_texts if t)

    print("  [PDF] Step2: LLM分批生成替换词表（每3页一批）...")
    # 按3页一批调用LLM，合并词表（先出现的条目优先，不覆盖）
    PAGES_PER_CHUNK = 3
    mapping = {}
    total_pages = len(page_texts)
    chunk_count = 0
    for chunk_start in range(0, total_pages, PAGES_PER_CHUNK):
        chunk_end = min(chunk_start + PAGES_PER_CHUNK, total_pages)
        chunk_pages = page_texts[chunk_start:chunk_end]
        chunk_text = "\n".join(t for t in chunk_pages if t).strip()
        if not chunk_text:
            continue
        chunk_count += 1
        page_range = f"第{chunk_start+1}-{chunk_end}页"
        print(f"    [{page_range}] 调用LLM（{len(chunk_text)}字）...")
        chunk_mapping = build_replacement_mapping(
            chunk_text, "", tgt_province, tgt_authority, config,
            src_authorities=src_authorities
        )
        # 合并：新批次的条目不覆盖已有条目（先出现的优先）
        new_count = 0
        for k, v in chunk_mapping.items():
            if k not in mapping:
                mapping[k] = v
                new_count += 1
        print(f"    [{page_range}] 新增{new_count}条，累计{len(mapping)}条")
    print(f"  替换词表（共{len(mapping)}条，来自{chunk_count}批）:")
    for k, v in mapping.items():
        print(f"    [{k}] → [{v}]")

    # ── 正则兜底：补充 LLM 词表可能漏掉的内容 ──────────────────────────────
    import re as _re2

    _ABBR_MAP = {
        "广东": "粤", "江苏": "苏", "南京": "宁", "重庆": "渝",
        "浙江": "浙", "山东": "鲁", "河南": "豫", "湖北": "鄂",
        "湖南": "湘", "四川": "川", "陕西": "陕", "辽宁": "辽",
        "河北": "冀", "山西": "晋", "吉林": "吉", "黑龙江": "黑",
        "安徽": "皖", "江西": "赣", "贵州": "黔", "云南": "滇",
        "甘肃": "甘", "青海": "青", "广西": "桂", "海南": "琼",
        "西藏": "藏", "新疆": "新", "内蒙古": "蒙", "宁夏": "宁",
        "上海": "沪", "北京": "京", "天津": "津",
        # 常见城市（有文号简称需求的）
        "广州": "穗", "深圳": "深", "成都": "蓉", "武汉": "汉",
        "杭州": "杭", "西安": "西", "苏州": "苏", "宁波": "甬",
        "无锡": "锡", "沈阳": "沈", "哈尔滨": "哈", "长沙": "长",
        "福州": "榕", "厦门": "厦", "泉州": "泉",
        "福建": "闽",
    }

    # 动态推导目标省简称（从 _ABBR_MAP 查找，找不到则取省份名第一个字）
    _tgt_bare = _re2.sub(r'[省市自治区自治州]+$', '', tgt_province)
    _tgt_abbr = _ABBR_MAP.get(_tgt_bare, _tgt_bare[0] if _tgt_bare else tgt_province[0])

    # 推导需要替换的省市地名
    _src_geo_names = set()
    _src_abbrs = set()
    if src_authorities:
        for auth in src_authorities:
            # 跳过中共开头
            auth_stripped = _re2.sub(r'^中共', '', auth)
            # 先尝试标准省/市/自治区格式
            m = _re2.match(r'^([\u4e00-\u9fff]{2,6}(?:省|市|自治区|自治州))', auth_stripped)
            if m:
                geo = m.group(1)
                bare = _re2.sub(r'[省市自治区自治州]+$', '', geo)
                if bare not in _ABBR_MAP:
                    continue
                _src_geo_names.add(geo)
                _src_geo_names.add(bare)
                abbr = _ABBR_MAP.get(bare)
                if abbr and abbr != _tgt_abbr:
                    _src_abbrs.add(abbr)
            else:
                # 标准格式匹不上时，尝试以已知省市裸名开头（如"重庆高新区..."）
                for known_bare in sorted(_ABBR_MAP.keys(), key=lambda x: -len(x)):
                    if auth_stripped.startswith(known_bare) and known_bare != _tgt_bare:
                        _src_geo_names.add(known_bare)
                        abbr = _ABBR_MAP.get(known_bare)
                        if abbr and abbr != _tgt_abbr:
                            _src_abbrs.add(abbr)
                        break

    _extra_count = 0
    _tgt_province_bare = _re2.sub(r'[省市自治区]+$', '', tgt_province)

    # 正则兜底暂时禁用
    _REGEX_FALLBACK_ENABLED = False

    if _REGEX_FALLBACK_ENABLED:
     # 1. 文号正则：简称[可选括号机构]汉字〔年〕编号号
     _all_abbrs = ''.join(_src_abbrs) if _src_abbrs else '粤苏宁渝浙鲁豫鄂湘川陕辽冀晋吉黑皖赣黔滇甘青桂琼藏新蒙沪京津'
     _DOC_NO_PAT = _re2.compile(
         r'([' + _all_abbrs + r']'
         r'(?:[（(][^）)]{1,10}[）)])?'
         r'[\u4e00-\u9fff]{1,8}'
         r'[〔\[【「『]'
         r'\d{4}'
         r'[〕\]】」』]'
         r'[\d\s]{1,8}'
         r'号)'
     )
     for m in _DOC_NO_PAT.finditer(raw_text):
         doc_no = m.group(1)
         first_char = doc_no[0]
         if first_char != _tgt_abbr and doc_no not in mapping:
             new_doc_no = _tgt_abbr + doc_no[1:]
             mapping[doc_no] = new_doc_no
             print(f"  [正则兜底-文号] [{doc_no}] → [{new_doc_no}]")
             _extra_count += 1

     # 2. 地名兜底：省市名（长词优先，避免短词先占）
     for geo in sorted(_src_geo_names, key=lambda x: -len(x)):
         if geo in mapping or not geo:
             continue
         if geo not in raw_text:
             continue
         bare = _re2.sub(r'[省市自治区自治州]+$', '', geo)
         suffix = geo[len(bare):]
         if bare == _tgt_province_bare:
             continue
         if suffix == '市':
             new_geo = tgt_province
         elif suffix in ('省', ''):
             new_geo = tgt_province if suffix == '省' else _tgt_province_bare
         else:
             new_geo = tgt_province
         mapping[geo] = new_geo
         print(f"  [正则兜底-地名] [{geo}] → [{new_geo}]")
         _extra_count += 1

    if _extra_count:
        print(f"  正则兜底共补充 {_extra_count} 条")

    # Explicit workflow mappings are authoritative and override LLM/regex guesses.
    mapping.update(explicit_mapping or {})

    if not mapping:
        print("  无需替换内容，跳过")
        doc.close()
        return pdf_path

    print("  [PDF] Step3: 从 span 精确读取样式，颜色mask精确遮盖 + 拉伸写回...")
    total_replaced = 0

    # 预渲染每页 pixmap（3x分辨率），用于红色像素边界检测
    page_pixmaps = {}
    _scale = 3.0
    for _pn, _pg in enumerate(doc):
        _mat = fitz.Matrix(_scale, _scale)
        _pix = _pg.get_pixmap(matrix=_mat, alpha=False)
        _arr = __import__('numpy').frombuffer(_pix.samples, dtype=__import__('numpy').uint8).reshape(_pix.height, _pix.width, 3)
        page_pixmaps[_pn] = _arr

    def _red_ink_bounds(arr, bx0, by0, bx1, by1, scale, up_ratio=0.5):
        """用红色像素投影找笔画实际上下边界，返回 PDF 坐标。
        只向上扩展搜索范围（笔画可能超出 bbox 顶部），
        下边界严格限制在 by1 + 2pt，避免将红头下方横线纳入遮盖区域。
        """
        import numpy as np
        ch_h = by1 - by0
        px0 = max(int(bx0 * scale), 0)
        px1 = min(int(bx1 * scale), arr.shape[1])
        # 上方扩展 up_ratio 倍字高，下方只多 2pt
        py0 = max(int((by0 - ch_h * up_ratio) * scale), 0)
        py1 = min(int((by1 + 2) * scale), arr.shape[0])
        if px1 <= px0 or py1 <= py0:
            return by0, by1
        region = arr[py0:py1, px0:px1]
        mask = (region[:, :, 0] > 150) & (region[:, :, 1] < 80) & (region[:, :, 2] < 80)
        rows = np.where(np.any(mask, axis=1))[0]
        if len(rows) == 0:
            return by0, by1
        ink_y0 = (py0 + rows[0]) / scale - 1      # 上边界留 1pt 余量
        ink_y1 = (py0 + rows[-1]) / scale + 1     # 下边界留 1pt 余量
        # 下边界不得超过 by1 + 2pt，防止误盖红色横线
        ink_y1 = min(ink_y1, by1 + 2)
        return ink_y0, ink_y1

    for page_num, page in enumerate(doc):
        page_replaced = 0
        arr = page_pixmaps.get(page_num)

        # 读取本页红色横线的最大宽度，用于后续红头补丁压缩判断
        _red_rule_width = 0.0
        for _d in page.get_drawings():
            _c = _d.get("color") or _d.get("stroke_color")
            if _c and round(_c[0], 2) == 1.0 and round(_c[1], 2) == 0.0 and round(_c[2], 2) == 0.0:
                _r = _d["rect"]
                # 横线：宽远大于高
                if _r.width > _r.height * 5:
                    _red_rule_width = max(_red_rule_width, _r.width)
        if _red_rule_width > 0:
            print(f"    第{page_num+1}页红线宽度: {_red_rule_width:.1f}pt")

        # 用 rawdict 获取每个字符的精确 bbox 和 origin
        raw_blocks = page.get_text("rawdict")["blocks"]

        # char_map: [(char, origin_x, origin_y, bbox, font, size, color_int, line_idx, line_bbox), ...]
        # line_first_style: {line_idx: (font, size, color_int)}
        # line_bbox_map:    {line_idx: (x0, y0, x1, y1)} — 整行渲染范围，用于整行重写时均匀分配 x
        char_map = []
        line_first_style = {}
        line_bbox_map = {}
        line_idx_counter = 0

        for block in raw_blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                lb = line.get("bbox", (0, 0, 0, 0))
                line_bbox_map[line_idx_counter] = lb
                # 记录本行第一个字符所在 span 的样式
                for span in line.get("spans", []):
                    if span.get("chars"):
                        line_first_style[line_idx_counter] = (
                            span.get("font", "FangSong"),
                            span.get("size", 12.0),
                            span.get("color", 0),
                        )
                        break
                for span in line.get("spans", []):
                    sf = span.get("font", "FangSong")
                    ss = span.get("size", 12.0)
                    sc = span.get("color", 0)
                    for ch in span.get("chars", []):
                        char_map.append((
                            ch["c"],
                            ch["origin"][0],
                            ch["origin"][1],
                            ch["bbox"],
                            sf, ss, sc,
                            line_idx_counter,
                            lb,
                        ))
                line_idx_counter += 1

        # ── 动态识别标题行和正文行 ────────────────────────────────────
        # 统计各行字号、字体、颜色，动态识别标题行
        line_stats = {}  # {line_idx: {'size': float, 'font': str, 'color': int, 'y_pos': float, 'is_centered': bool}}
        for line_idx, (sf, ss, sc) in line_first_style.items():
            lb = line_bbox_map.get(line_idx, (0, 0, 0, 0))
            # 判断是否居中：行中心距离页面中心的比例
            page_width = page.rect.width
            line_center_x = (lb[0] + lb[2]) / 2
            page_center_x = page_width / 2
            center_deviation = abs(line_center_x - page_center_x) / page_width
            is_centered = center_deviation < 0.15  # 中心偏差<15%视为居中
            line_stats[line_idx] = {
                'size': float(ss),
                'font': sf,
                'color': sc,
                'y_pos': lb[1],  # 顶部y坐标
                'is_centered': is_centered,
            }

        # 统计字号分布，找出正文字号（众数或中位数）
        all_sizes = [s['size'] for s in line_stats.values() if s['color'] == 0]  # 只统计黑色文字
        if all_sizes:
            from collections import Counter
            size_counts = Counter(all_sizes)
            # 正文字号：出现次数最多的字号
            body_size = size_counts.most_common(1)[0][0]
            # 标题字号阈值：明显大于正文（至少1.2倍）
            title_size_threshold = body_size * 1.2
            print(f"    [动态识别] 正文字号={body_size:.1f}pt, 标题阈值={title_size_threshold:.1f}pt")
        else:
            # 兜底：使用固定阈值
            title_size_threshold = 18.0
            print(f"    [动态识别] 未找到正文字号，使用固定阈值={title_size_threshold:.1f}pt")

        # 标记标题行
        title_line_indices_dynamic = set()
        for line_idx, stats in line_stats.items():
            is_title = (
                stats['color'] == 0 and  # 黑色
                stats['size'] >= title_size_threshold and  # 字号明显大于正文
                stats['y_pos'] < page.rect.height * 0.5  # 在页面上半部分
            )
            if is_title:
                title_line_indices_dynamic.add(line_idx)
                print(f"    [动态识别] 标题行{line_idx}: 字号={stats['size']:.1f}pt, 字体={stats['font']}, 居中={stats['is_centered']}")

        # replace_ops_char = [(redact_rect, new_char, font_path, font_key, font_size, color_rgb, origin_x, origin_y), ...]
        replace_ops_char = []
        covered_char_indices = set()  # 防止同一字符被多个词条覆盖（防重影）

        for orig_word, new_word in sorted(mapping.items(), key=lambda x: -len(x[0])):
            # 在 char_map 里找匹配 orig_word 的字符序列
            # 支持字间有空格的情况（如红头字间距排版：南 京 市 规 划 局）
            # 两边都去掉空格：orig_chars 和 char_map 中的空格均跳过
            orig_chars = [c for c in orig_word if c != ' ']
            n = len(orig_chars)
            found_sequences = []

            i = 0
            while i < len(char_map):
                # 跳过已被其他词条覆盖的字符
                if i in covered_char_indices:
                    i += 1
                    continue
                # 从位置i开始，跳过空格，尝试匹配orig_chars
                matched = []
                j = i
                k = 0
                while k < n and j < len(char_map):
                    ch_c = char_map[j][0]
                    if ch_c == ' ':  # 跳过 char_map 中的空格
                        j += 1
                        continue
                    if ch_c == orig_chars[k]:
                        matched.append((j, char_map[j]))
                        j += 1
                        k += 1
                    else:
                        break
                if k == n and matched:
                    # 全部匹配，记录实际字符位置（含中间空格）
                    all_indices = list(range(matched[0][0], matched[-1][0] + 1))
                    seq = [char_map[idx] for idx in all_indices if char_map[idx][0] != ' ']
                    # 确认没有被覆盖
                    if not any(idx in covered_char_indices for idx in all_indices):
                        found_sequences.append((i, seq, all_indices))
                        for idx in all_indices:
                            covered_char_indices.add(idx)
                        i = matched[-1][0] + 1
                        continue
                i += 1

            if not found_sequences:
                continue

            new_chars = [c for c in new_word if c != ' ']
            m = len(new_chars)

            for start_idx, seq, all_indices in found_sequences:
                # 取该词第一个字的实际字体（seq[0][4]）和行首样式（用于判断红头/标题）
                ch_line_idx = seq[0][7]
                line_sf, line_ss, line_sc = line_first_style.get(ch_line_idx, (seq[0][4], seq[0][5], seq[0][6]))

                # 词条自己的字体、字号、颜色
                sf = seq[0][4]  # 该词第一个字符的实际字体
                ss = seq[0][5]  # 该词第一个字符的实际字号
                sc = seq[0][6]  # 该词第一个字符的实际颜色

                r_val = ((sc >> 16) & 0xFF) / 255.0
                g_val = ((sc >> 8)  & 0xFF) / 255.0
                b_val = ( sc        & 0xFF) / 255.0
                color_rgb = (r_val, g_val, b_val)

                # 判断是否为红头/标题行：用动态识别结果
                is_red_line   = (line_sc == 16711680)
                is_title_line = (ch_line_idx in title_line_indices_dynamic)

                # 正文/文号行统一用仿宋_GB2312，避免嵌入子集字体识别错误；红头和标题行保留原字体
                if is_red_line or is_title_line:
                    # 红头和标题行：使用该词第一个字符的实际字体
                    font_path = FONT_MAP.get(sf)
                    if not font_path or not os.path.exists(font_path):
                        # 模糊匹配
                        sf_lower = sf.lower()
                        if "fzxbs" in sf_lower or "xiaobiao" in sf_lower or "小标宋" in sf:
                            font_path = FONT_MAP.get("FZXBSK", FONT_MAP["FangSong_GB2312"])
                        elif "fangsong" in sf_lower or "仿宋" in sf_lower:
                            font_path = FONT_MAP["FangSong_GB2312"]
                        elif "hei" in sf_lower or "黑" in sf_lower:
                            font_path = FONT_MAP["SimHei"]
                        elif "song" in sf_lower or "宋" in sf_lower:
                            font_path = FONT_MAP["SimSun"]
                        else:
                            font_path = FONT_MAP.get("FZXBSK", FONT_MAP["FangSong_GB2312"])
                    # 使用不同的fontname：红头行用"RedHeadFont"，标题行用"TitleFont"
                    if is_red_line:
                        font_key = "RedHeadFont"
                    else:
                        font_key = "TitleFont"
                else:
                    font_path = FONT_MAP["FangSong_GB2312"]
                    font_key  = "FangSongGB"

                font_size = float(ss)

                # 计算每个新字的插入位置
                is_red_line = (sc == 16711680)
                if is_red_line:
                    # 红头行：均匀分配在原词占据的 x 范围内，消除 HorizScaling 导致的拥挤
                    x0_first = seq[0][3][0]   # bbox 左边
                    x1_last  = seq[-1][3][2]  # bbox 右边
                    total_w  = x1_last - x0_first
                    char_w   = total_w / m if m > 0 else total_w
                    base_y   = seq[0][2]
                    positions = [
                        (x0_first + j * char_w, base_y,
                         (x0_first + j * char_w,
                          seq[0][3][1],
                          x0_first + (j + 1) * char_w,
                          seq[0][3][3]))
                        for j in range(m)
                    ]
                elif m == n:
                    # 等长非红头：新字逐一对齐原字位置
                    positions = [(seq[j][1], seq[j][2], seq[j][3]) for j in range(n)]
                else:
                    # 新词字数不等（非红头）
                    extra = m - n  # 正数=新词更多字，负数=新词更少字
                    if extra <= 0:
                        # 新词更短或等长：均匀压缩进原词总宽度
                        x0_first = seq[0][1]
                        x0_last  = seq[-1][1]
                        total_w  = x0_last - x0_first + (seq[-1][3][2] - seq[-1][3][0])
                        char_w   = total_w / m if m > 0 else total_w
                        base_y   = seq[0][2]
                        positions = [
                            (x0_first + j * char_w, base_y,
                             (x0_first + j * char_w,
                              seq[0][3][1],
                              x0_first + (j + 1) * char_w,
                              seq[0][3][3]))
                            for j in range(m)
                        ]
                        borrow_left  = []
                        borrow_right = []
                    else:
                        # 新词更多字：向左右借邻字，把整段重排
                        seq_line_idx = seq[0][7]
                        seq_color    = seq[0][6]
                        first_cm_idx = all_indices[0]
                        last_cm_idx  = all_indices[-1]

                        def _can_borrow(cm_idx):
                            """邻字可借：同行、同颜色、未被覆盖、非空格"""
                            if cm_idx < 0 or cm_idx >= len(char_map):
                                return False
                            ch = char_map[cm_idx]
                            return (ch[0] != ' ' and ch[7] == seq_line_idx
                                    and ch[6] == seq_color
                                    and cm_idx not in covered_char_indices)

                        # 向左借（最多4个）
                        borrow_left = []
                        li = first_cm_idx - 1
                        while len(borrow_left) < 4 and li >= 0:
                            if _can_borrow(li):
                                borrow_left.insert(0, li)
                            li -= 1

                        # 向右借（最多4个）
                        borrow_right = []
                        ri = last_cm_idx + 1
                        while len(borrow_right) < 4 and ri < len(char_map):
                            if _can_borrow(ri):
                                borrow_right.append(ri)
                            ri += 1

                        # 合并：左邻 + 原词 + 右邻，计算总 x 范围
                        all_seg = (
                            [char_map[i] for i in borrow_left] +
                            seq +
                            [char_map[i] for i in borrow_right]
                        )
                        total_chars = len(borrow_left) + m + len(borrow_right)
                        x0_seg = all_seg[0][3][0]   # bbox 左
                        x1_seg = all_seg[-1][3][2]  # bbox 右
                        seg_w  = x1_seg - x0_seg
                        char_w = seg_w / total_chars if total_chars > 0 else seg_w
                        base_y = seq[0][2]

                        # 新字插入位置：跳过左邻偏移，从原词对应段开始
                        left_offset = len(borrow_left)
                        positions = [
                            (x0_seg + (left_offset + j) * char_w, base_y,
                             (x0_seg + (left_offset + j) * char_w,
                              seq[0][3][1],
                              x0_seg + (left_offset + j + 1) * char_w,
                              seq[0][3][3]))
                            for j in range(m)
                        ]

                        # 将左右邻字也标记覆盖（需要重写它们）
                        for bi in borrow_left + borrow_right:
                            covered_char_indices.add(bi)

                        # 重写左邻字（原字符不变，只更新位置，字体与主词一致）
                        for k_b, bi in enumerate(borrow_left):
                            bch = char_map[bi]
                            bx = x0_seg + k_b * char_w
                            boy = bch[2]
                            br, bg_, bb_ = ((bch[6] >> 16) & 0xFF) / 255.0, ((bch[6] >> 8) & 0xFF) / 255.0, (bch[6] & 0xFF) / 255.0
                            _er_b = fitz.Rect(bch[3][0], bch[3][1], bch[3][2], bch[3][3])
                            replace_ops_char.append((_er_b, bch[0], font_path, font_key, float(bch[5]), (br, bg_, bb_), bx, boy, (1.0, 1.0)))

                        # 重写右邻字
                        for k_b, bi in enumerate(borrow_right):
                            bch = char_map[bi]
                            bx = x0_seg + (left_offset + m + k_b) * char_w
                            boy = bch[2]
                            br, bg_, bb_ = ((bch[6] >> 16) & 0xFF) / 255.0, ((bch[6] >> 8) & 0xFF) / 255.0, (bch[6] & 0xFF) / 255.0
                            _er_b = fitz.Rect(bch[3][0], bch[3][1], bch[3][2], bch[3][3])
                            replace_ops_char.append((_er_b, bch[0], font_path, font_key, float(bch[5]), (br, bg_, bb_), bx, boy, (1.0, 1.0)))

                # erase rect：每个原字单独擦除（用原字的 bbox）
                # 红头行用颜色mask精确边界，其余行精确匹配bbox
                def _make_char_erase_rect(ch_seq_item, is_red_ln):
                    cx0, cy0, cx1, cy1 = ch_seq_item[3]
                    if is_red_ln and arr is not None:
                        iy0, iy1 = _red_ink_bounds(arr, cx0, cy0, cx1, cy1, _scale)
                        return fitz.Rect(cx0, iy0, cx1, iy1)
                    return fitz.Rect(cx0, cy0, cx1, cy1)

                # 红头行：用 span 原始字号，不拉伸
                _line_lb = line_bbox_map.get(ch_line_idx, None)
                _line_h = (_line_lb[3] - _line_lb[1]) if _line_lb else (seq[0][3][3] - seq[0][3][1])
                if is_red_line:
                    _fs_unified = font_size
                    _sy_unified = 1.0

                for j, new_char in enumerate(new_chars):
                    if j < len(positions):
                        ox, oy, pos_bbox = positions[j]
                    else:
                        break
                    # 每个新字对应擦除原词第j个字
                    if j < n:
                        _er = _make_char_erase_rect(seq[j], is_red_line)
                    elif j == n - 1:
                        _er = _make_char_erase_rect(seq[n - 1], is_red_line)
                    else:
                        _er = None
                    if is_red_line:
                        replace_ops_char.append((
                            _er,
                            new_char, font_path, font_key,
                            _fs_unified, color_rgb, ox, oy,
                            (1.0, _sy_unified),  # 不拉伸
                        ))
                    else:
                        replace_ops_char.append((
                            _er,
                            new_char, font_path, font_key,
                            font_size, color_rgb, ox, oy,
                            (1.0, 1.0),
                        ))

        # 整行重写：红色行或标题行有字符被替换时，把整行其余字也用系统字体重写，保持视觉一致
        replaced_line_indices = {
            char_map[idx][7]
            for idx in covered_char_indices
            if idx < len(char_map)
        }
        # 对于每字单独成行的红头（字间距排版），按 oy 分组：已替换字符的 oy
        replaced_red_oys = set()
        for idx in covered_char_indices:
            if idx < len(char_map):
                ch_sc_val = char_map[idx][6]  # ch_sc
                r_ = (ch_sc_val >> 16) & 0xFF
                g_ = (ch_sc_val >> 8) & 0xFF
                b_ = ch_sc_val & 0xFF
                if r_ > 200 and g_ < 50 and b_ < 50:
                    replaced_red_oys.add(round(char_map[idx][2], 1))  # ch_oy

        def _is_title_line(line_sc, line_ss, line_sf):
            """判断是否为标题行：使用动态识别结果。"""
            # 查找该行在 line_first_style 中的索引
            for line_idx, (sf, ss, sc) in line_first_style.items():
                if sf == line_sf and abs(float(ss) - float(line_ss)) < 0.01 and sc == line_sc:
                    return line_idx in title_line_indices_dynamic
            return False

        # 收集所有标题行的行号（已在动态识别中完成）
        title_line_indices = title_line_indices_dynamic

        # 逐字处理标题行和红头行
        for ci, ch_info in enumerate(char_map):
            ch_c, ch_ox, ch_oy, ch_bbox, ch_sf, ch_ss, ch_sc, ch_li, ch_lb = ch_info

            line_sf, line_ss, line_sc = line_first_style.get(ch_li, (ch_sf, ch_ss, ch_sc))
            is_red = (line_sc == 16711680)
            is_title = _is_title_line(line_sc, line_ss, line_sf)

            # 标题行：所有行都处理；红头行：只处理被替换的行
            if is_title:
                # 标题行全部重写，不检查 replaced_line_indices
                pass
            elif is_red and (ch_li in replaced_line_indices or round(ch_oy, 1) in replaced_red_oys):
                # 红头行：按 line_idx 或 oy 匹配（兼容每字单独成行的字间距红头排版）
                pass
            else:
                # 既不是标题行，也不是被替换的红头行，跳过
                continue

            # 已被词条处理过的字符跳过（由词条负责它的 redact+写回）
            if ci in covered_char_indices:
                continue

            r = ((line_sc >> 16) & 0xFF) / 255.0
            g = ((line_sc >> 8)  & 0xFF) / 255.0
            b = ( line_sc        & 0xFF) / 255.0

            # 黑色标题行：所有字符按原字体、原坐标重写
            if is_title:
                # 使用该字符原始字体和样式
                fp = FONT_MAP.get(ch_sf)
                if not fp or not os.path.exists(fp):
                    # 模糊匹配原字体
                    ch_sf_lower = ch_sf.lower()
                    if "fzxbs" in ch_sf_lower or "xiaobiao" in ch_sf_lower or "小标宋" in ch_sf:
                        fp = FONT_MAP.get("FZXBSK", FONT_MAP["SimSun"])
                    elif "song" in ch_sf_lower or "宋" in ch_sf_lower or "simsun" in ch_sf_lower:
                        fp = FONT_MAP["SimSun"]
                    elif "hei" in ch_sf_lower or "黑" in ch_sf_lower:
                        fp = FONT_MAP["SimHei"]
                    elif "kai" in ch_sf_lower or "楷" in ch_sf_lower:
                        fp = FONT_MAP["KaiTi_GB2312"]
                    elif "times" in ch_sf_lower or "roman" in ch_sf_lower:
                        fp = FONT_MAP.get("TimesNewRoman", FONT_MAP["SimSun"])
                    else:
                        fp = FONT_MAP["SimSun"]  # 兜底
                # 使用新的fontname避免与PDF中已有字体冲突
                fk = "TitleFont"  # 统一使用新名称，强制使用fontfile
                fs_r = float(ch_ss)  # 使用字符原始字号
                rr = fitz.Rect(ch_bbox[0], ch_bbox[1], ch_bbox[2], ch_bbox[3])
                ox = ch_ox  # 使用原始x坐标
                oy = ch_oy  # 使用原始y坐标
                stretch = (1.0, 1.0)  # 无拉伸
                replace_ops_char.append((rr, ch_c, fp, fk, fs_r, (r, g, b), ox, oy, stretch))
                continue

            # 红头行：收集字符信息，后续统一居中处理
            _line_lb_r = line_bbox_map.get(ch_li, None)
            _line_h_r = (_line_lb_r[3] - _line_lb_r[1]) if _line_lb_r else max(int(ch_bbox[3] - ch_bbox[1]), 1)

            # 红头行：用颜色mask精确遮盖，统一用 span 原始字号，不拉伸
            if arr is not None:
                ink_y0, ink_y1 = _red_ink_bounds(arr, ch_bbox[0], ch_bbox[1], ch_bbox[2], ch_bbox[3], _scale)
                rr = fitz.Rect(ch_bbox[0], ink_y0, ch_bbox[2], ink_y1)
            else:
                rr = fitz.Rect(ch_bbox[0], ch_bbox[1], ch_bbox[2], ch_bbox[3])

            # 获取红头字体（从行首字体line_sf）
            lf_lower = line_sf.lower()
            fp = FONT_MAP.get(line_sf)
            if not fp or not os.path.exists(fp):
                # 模糊匹配
                if "fzxbs" in lf_lower or "xiaobiao" in lf_lower:
                    # 方正小标宋：使用FONT_MAP中配置的路径
                    fp = FONT_MAP.get("FZXBSK", FONT_MAP["FangSong_GB2312"])
                elif "fangsong" in lf_lower or "仿宋" in lf_lower:
                    fp = FONT_MAP["FangSong_GB2312"]
                elif "hei" in lf_lower or "黑" in lf_lower:
                    fp = FONT_MAP["SimHei"]
                elif "song" in lf_lower or "宋" in lf_lower:
                    fp = FONT_MAP["SimSun"]
                else:
                    # 兜底：红头用方正小标宋
                    fp = FONT_MAP.get("FZXBSK", FONT_MAP["FangSong_GB2312"])
            # 使用固定fontname避免与PDF嵌入字体冲突
            fk = "RedHeadFont"

            fs_r = float(line_ss)
            # 不拉伸，保持原字号
            stretch = (1.0, 1.0)

            ox = ch_ox  # 使用原始x坐标
            replace_ops_char.append((rr, ch_c, fp, fk, fs_r, (r, g, b), ox, ch_oy, stretch))

        if not replace_ops_char:
            continue

        # ── 红头行：按 line_idx 分组，取首字字号/拉伸，居中写回 ──────────────
        # 把所有 ops 按行分组，红头行（color=红色）单独走居中路径
        from collections import defaultdict as _dd
        from PIL import ImageFont as _IFc, Image as _Imc, ImageDraw as _Drc

        red_line_ops   = _dd(list)   # {line_oy: [op, ...]}  按基线 y 分组
        other_ops      = []

        for op in replace_ops_char:
            _, new_char, fp, fk, fs, color_rgb, ox, oy, stretch_info = op
            r_, g_, b_ = color_rgb
            is_red_op = (abs(r_ - 1.0) < 0.01 and g_ < 0.1 and b_ < 0.1)
            if is_red_op:
                red_line_ops[round(oy, 1)].append(op)
            else:
                other_ops.append(op)

        # 先处理非红头行：redact 删除文字层 + 白色覆盖 + 写回
        for op in other_ops:
            erase_rect, new_char, fp, fk, fs, color_rgb, ox, oy, stretch_info = op
            if erase_rect is not None:
                # 添加 redact 标注以删除原文字层
                page.add_redact_annot(erase_rect, fill=(1, 1, 1))
        # 应用所有 redact 标注
        page.apply_redactions()

        for op in other_ops:
            erase_rect, new_char, fp, fk, fs, color_rgb, ox, oy, stretch_info = op
            try:
                morph = None
                if isinstance(stretch_info, tuple):
                    sx, sy = stretch_info
                    if sx != 1.0 or sy != 1.0:
                        morph = (fitz.Point(ox, oy), fitz.Matrix(sx, 0, 0, sy, 0, 0))
                elif stretch_info != 1.0:
                    morph = (fitz.Point(ox, oy), fitz.Matrix(stretch_info, 0, 0, 1, 0, 0))

                page.insert_text((ox, oy), new_char, fontname=fk, fontfile=fp,
                                 fontsize=fs, color=color_rgb, morph=morph)
                page_replaced += 1
            except Exception as e:
                print(f"    写入失败 [{new_char}] 第{page_num+1}页: {e}")

        # 红头行：统一居中处理，保持与原红头相同的总宽度
        if red_line_ops:
            print(f"      [红头处理] 共{len(red_line_ops)}行红头")

            # ── Pass 1：计算每行的写入参数，同时收集所有 redact 矩形 ──
            red_line_write_plans = []  # [(line_cx, base_oy, actual_fontsize, char_spacing, char_widths, ops)]

            for base_oy, ops in red_line_ops.items():
                if not ops:
                    continue

                print(f"      [红头] 处理{len(ops)}个字符的红头行")

                # 1. 获取第一个字符的字体和拉伸参数（全行统一）
                _, _, fp0, fk0, fs0, color_rgb0, _, _, stretch0 = ops[0]

                # 2. 计算原红头行的范围
                all_rects = [op[0] for op in ops if op[0] is not None]
                if not all_rects:
                    print(f"      [红头] 警告：没有有效的矩形区域，跳过")
                    continue

                line_x0 = min(r.x0 for r in all_rects)
                line_x1 = max(r.x1 for r in all_rects)
                line_y0 = min(r.y0 for r in all_rects)
                line_y1 = max(r.y1 for r in all_rects)
                line_cx = (line_x0 + line_x1) / 2
                orig_text_width = line_x1 - line_x0

                if _red_rule_width > 0:
                    # 有红线时：若原行宽度明显小于红线宽度（不足60%），
                    # 说明该行是较短的副行，保留原行宽度避免过度拉伸超出页面
                    if orig_text_width < _red_rule_width * 0.6:
                        target_width = orig_text_width
                        print(f"      [红头] 原红头文字宽度={orig_text_width:.1f}pt, 红线宽度={_red_rule_width:.1f}pt, 行较短保留原宽度")
                    else:
                        target_width = _red_rule_width
                        print(f"      [红头] 原红头文字宽度={orig_text_width:.1f}pt, 红线宽度={_red_rule_width:.1f}pt, 使用红线宽度作为目标")
                else:
                    target_width = orig_text_width
                    print(f"      [红头] 原红头文字宽度={target_width:.1f}pt (无红线)")

                if _red_rule_width > 0 and orig_text_width >= _red_rule_width * 0.6:
                    page_center_x = page.rect.width / 2
                    line_cx = page_center_x
                    print(f"      [红头] 使用页面中心 x={page_center_x:.1f}")
                else:
                    print(f"      [红头] 使用原红头中心 x={line_cx:.1f}")

                # 3. 收集 redact 矩形（不立即 apply）
                redact_rect = fitz.Rect(line_x0, line_y0, line_x1, line_y1)
                page.add_redact_annot(redact_rect, fill=(1, 1, 1))

                # 4. 用PIL测量新红头每个字的宽度
                _tmp_img = _Imc.new("RGB", (2000, 200))
                _tmp_drw = _Drc.Draw(_tmp_img)

                char_widths = []
                for op in ops:
                    _, ch, fp_char, *_ = op
                    try:
                        _pil_f_char = _IFc.truetype(fp_char, fs0)
                        bb = _tmp_drw.textbbox((0, 0), ch, font=_pil_f_char)
                        char_widths.append(max(bb[2] - bb[0], 1))
                    except Exception:
                        char_widths.append(fs0)

                total_char_width = sum(char_widths)
                print(f"      [红头] 新文本测量总宽度 = {total_char_width:.1f}pt（各字符使用各自字体）")

                # 5. 计算字号和字间距
                actual_fontsize = fs0
                char_spacing = 0

                if total_char_width > target_width:
                    actual_fontsize = fs0 * (target_width / total_char_width)
                    print(f"      [红头] 新文本过宽，缩小字号：{fs0:.2f}pt → {actual_fontsize:.2f}pt")

                    char_widths = []
                    for op in ops:
                        _, ch, fp_char, *_ = op
                        try:
                            _pil_f_adjusted = _IFc.truetype(fp_char, actual_fontsize)
                            bb = _tmp_drw.textbbox((0, 0), ch, font=_pil_f_adjusted)
                            char_widths.append(max(bb[2] - bb[0], 1))
                        except Exception:
                            char_widths.append(actual_fontsize)

                    total_char_width = sum(char_widths)
                    char_spacing = 0
                    print(f"      [红头] 调整后总宽度 = {total_char_width:.1f}pt，字间距 = 0")
                else:
                    if len(ops) > 1:
                        char_spacing = (target_width - total_char_width) / (len(ops) - 1)
                    else:
                        char_spacing = 0
                    print(f"      [红头] 字号保持 {fs0:.2f}pt，字间距 = {char_spacing:.1f}pt")

                new_total_width = total_char_width + char_spacing * (len(ops) - 1)
                print(f"      [红头] 最终: 字宽={total_char_width:.1f}pt, 字间距={char_spacing:.1f}pt, 总宽={new_total_width:.1f}pt")

                red_line_write_plans.append((line_cx, new_total_width, actual_fontsize, char_spacing, char_widths, ops))

            # ── Pass 2：所有红头行 redact 一次性 apply，再统一写入 ──
            page.apply_redactions()
            print(f"      [红头] 已删除所有红头区域")

            for line_cx, new_total_width, actual_fontsize, char_spacing, char_widths, ops in red_line_write_plans:
                start_x = line_cx - new_total_width / 2
                x_cursor = start_x
                for idx_op, op in enumerate(ops):
                    _, new_char, fp, fk, fs, color_rgb, _, oy, stretch_info = op
                    cw = char_widths[idx_op]
                    try:
                        page.insert_text((x_cursor, oy), new_char, fontname=fk, fontfile=fp,
                                         fontsize=actual_fontsize, color=color_rgb)
                        page_replaced += 1
                        if idx_op < len(ops) - 1:
                            x_cursor += cw + char_spacing
                        else:
                            x_cursor += cw
                    except Exception as e:
                        print(f"    [红头] 写入失败 [{new_char}]: {e}")
                print(f"      [红头] 完成写入，最终x={x_cursor:.1f}")

        if page_replaced:
            print(f"    第{page_num+1}页：替换{page_replaced}处")
        total_replaced += page_replaced

    out_path = output_dir / f"{pdf_path.stem}_localized.pdf"
    doc.save(str(out_path))
    doc.close()
    print(f"  ✓ PDF输出: {out_path} （共替换{total_replaced}处）")

    # 生成PNG预览图
    print(f"  [PDF] 生成PNG预览图...")
    doc_preview = fitz.open(str(out_path))
    preview_count = len(doc_preview)
    for page_num in range(preview_count):
        page = doc_preview[page_num]
        # 使用2倍分辨率渲染，提高清晰度
        mat = fitz.Matrix(2.0, 2.0)
        pix = page.get_pixmap(matrix=mat)

        # 保存为PNG
        png_path = output_dir / f"{pdf_path.stem}_localized_page{page_num + 1}.png"
        pix.save(str(png_path))
        print(f"    第{page_num + 1}页 → {png_path.name}")

    doc_preview.close()
    print(f"  ✓ 共生成 {preview_count} 张PNG预览图")

    return out_path


# ─────────────────────────────────────────────
# ─────────────────────────────────────────────
# 发文主体识别
# ─────────────────────────────────────────────

def detect_issuing_authority_from_pdf(pdf_path: Path, config: dict = None) -> str:
    """从PDF识别发文主体。
    路径0：LLM从第一页文字识别（最准确，需要config）
    路径1：落款文字层（日期行上方）
    路径2：文字层红色 span
    路径3：矢量路径红色填充矩形 + RapidOCR
    """
    if not FITZ_OK:
        return ""
    import re as _re
    _DATE_PAT = _re.compile(r'[○〇一二三四五六七八九十×X\d]{4}\s*年\s*[○〇×X一二三四五六七八九十\d]{1,2}\s*月')

    try:
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        page_h = page.rect.height
        page_w = page.rect.width

        # 找红线位置：最靠上那条横跨页面宽度60%以上的水平线（页面前40%内）
        # 页脚红线在80%以上，不会命中
        red_line_y = None
        for d in page.get_drawings():
            if d.get("type") == "l":
                pts = d.get("items", [])
                for item in pts:
                    if item[0] == "l":
                        p1, p2 = item[1], item[2]
                        if abs(p1.y - p2.y) < 3 and abs(p1.x - p2.x) > page_w * 0.6:
                            y_pos = (p1.y + p2.y) / 2
                            if y_pos / page_h < 0.40:
                                if red_line_y is None or y_pos < red_line_y:
                                    red_line_y = y_pos
        # 也从细长矩形检测红线（必须是红色填充或红色描边）
        for d in page.get_drawings():
            rect = d.get("rect")
            if not (rect and rect.height < 5 and rect.width > page_w * 0.6):
                continue
            if rect.y0 / page_h >= 0.40:
                continue
            # 检查颜色：填充色或描边色为红色
            fill = d.get("fill") or (0, 0, 0)
            stroke = d.get("color") or (0, 0, 0)
            is_red = (
                (fill[0] > 0.6 and fill[1] < 0.3 and fill[2] < 0.3) or
                (stroke[0] > 0.6 and stroke[1] < 0.3 and stroke[2] < 0.3)
            )
            if is_red:
                if red_line_y is None or rect.y0 < red_line_y:
                    red_line_y = rect.y0

        # 提取红线以上的文字（兜底用页面前35%）
        cutoff_y = red_line_y if red_line_y else page_h * 0.35
        # 先收集 (y, text) 对
        raw_header_lines = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                if line["bbox"][1] < cutoff_y:
                    text = "".join(s["text"] for s in line["spans"]).strip()
                    y = line["bbox"][1]
                    if text:
                        raw_header_lines.append((y, text))
        # 把 y 坐标相近（±5pt）且为单字的行合并成一行（字间距红头场景）
        raw_header_lines.sort(key=lambda x: x[0])
        merged_lines = []
        i = 0
        while i < len(raw_header_lines):
            y0, t0 = raw_header_lines[i]
            # 收集同 y 行（±5pt 内）
            group = [(y0, t0)]
            j = i + 1
            while j < len(raw_header_lines) and abs(raw_header_lines[j][0] - y0) < 8:
                group.append(raw_header_lines[j])
                j += 1
            # 如果全是单字，合并
            if all(len(t) == 1 for _, t in group) and len(group) > 1:
                merged_lines.append("".join(t for _, t in group))
            else:
                for _, t in group:
                    merged_lines.append(t)
            i = j
        header_text = "\n".join(merged_lines)

        # 路径0：先用正则从 merged_lines 中提取机关名（不依赖LLM，准确率高）
        _ORG_PAT = _re.compile(
            r'^(?:(?:[\u4e00-\u9fff]{2,8}(?:省|市|区|县|自治区|自治州))+)'
            r'[\u4e00-\u9fff]{2,12}'
            r'(?:局|厅|委|办|院|部|署|社|站|队|中心|委员会|管理局|管理委员会|人民政府|党委|工作委员会)$'
        )
        regex_auths = [t for t in merged_lines if _ORG_PAT.match(t)]

        # 路径0b：LLM识别（补充正则未能覆盖的情况）
        if config and header_text:
            try:
                llm_result = _call_llm_json(
                    """你是一个政府公文分析助手。从给定的红头文字中识别所有发文主体（即发文机关名称）。
发文主体是红头区域中的机关名称，如"XX省XX厅"、"XX市XX局"、"XX市XX委"等。
【重要】联合发文时红头会有多行机关名，每行都是一个独立机关，必须全部列出。
例如红头有"南京市城市管理局"和"南京市规划局"两行，则输出两个机关。
不要把发文字号（如"宁城规字〔2018〕4号"）当成机关名。
不要把标题（如"关于印发...的通知"）当成机关名。
只输出纯JSON，格式：{"authorities": ["机关名1", "机关名2"]}
如果无法识别则输出：{"authorities": []}""",
                    f"红头文字：\n\n{header_text}",
                    config
                )
                llm_auths = llm_result.get("authorities", [])
            except Exception:
                llm_auths = []

            # 合并：以正则结果为基础，LLM结果补充正则未覆盖的
            merged_auths = list(regex_auths)
            for a in llm_auths:
                if a not in merged_auths:
                    merged_auths.append(a)
            if merged_auths:
                doc.close()
                return merged_auths

        if regex_auths:
            doc.close()
            return regex_auths

        # 路径1：落款 —— 找日期行，取其正上方行
        all_pages_lines = []
        for pg_idx in range(len(doc)):
            pg = doc[pg_idx]
            pg_h = pg.rect.height
            pg_lines = []
            for block in pg.get_text("dict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    y = line["bbox"][1]
                    line_text = "".join(s["text"] for s in line["spans"]).strip()
                    if line_text:
                        pg_lines.append((y, line_text, pg_h))
            pg_lines.sort(key=lambda x: x[0])
            all_pages_lines.append(pg_lines)

        for pg_lines in all_pages_lines:
            pg_h = pg_lines[0][2] if pg_lines else page_h
            for i, (y, text, _) in enumerate(pg_lines):
                if y / pg_h < 0.45:
                    continue
                if not _DATE_PAT.search(text):
                    continue
                if len(text) > 20:
                    continue
                if any(kw in text for kw in ('自', '截至', '起施行', '至今', '印发')):
                    continue
                orgs = []
                prev_collected_y = y
                for j in range(i - 1, -1, -1):
                    prev_y, prev_text, _ = pg_lines[j]
                    if not prev_text:
                        continue
                    if any(prev_text.startswith(p) for p in ('抄送', '抄报', '附件', '―', '-', '—')):
                        continue
                    if prev_collected_y - prev_y > 60:
                        break
                    org_part = prev_text.split()[0] if prev_text.split() else prev_text
                    if len(org_part) < 4:
                        break
                    orgs.append(org_part)
                    prev_collected_y = prev_y
                    if len(orgs) >= 5:
                        break
                if orgs:
                    doc.close()
                    return list(reversed(orgs))
                break

        # 路径2：文字层红色 span
        red_lines = []
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                y_pct = line["bbox"][1] / page_h
                if y_pct > 0.35:
                    continue
                line_text = "".join(s["text"] for s in line["spans"]).strip()
                if not line_text:
                    continue
                for span in line["spans"]:
                    c = span.get("color", 0)
                    r = (c >> 16) & 0xFF
                    g = (c >> 8) & 0xFF
                    b = c & 0xFF
                    if r >= 180 and g < 80 and b < 80:
                        red_lines.append((line["bbox"][1], line_text))
                        break

        if red_lines:
            red_lines.sort(key=lambda x: x[0])
            authority = red_lines[0][1]
        else:
            # 路径3：矢量路径红色填充矩形 + RapidOCR
            drawings = page.get_drawings()
            red_rects = []
            for d in drawings:
                fill = d.get("fill")
                if not fill:
                    continue
                fr, fg, fb = fill[0], fill[1], fill[2]
                if fr >= 0.9 and fg < 0.2 and fb < 0.2:
                    r = d["rect"]
                    if r.height > 20 and (r.y0 / page_h) < 0.40:
                        red_rects.append(r)
            if not red_rects:
                doc.close()
                return []
            x0 = min(r.x0 for r in red_rects) - 5
            y0 = min(r.y0 for r in red_rects) - 5
            x1 = max(r.x1 for r in red_rects) + 5
            y1 = max(r.y1 for r in red_rects) + 5
            clip = fitz.Rect(max(0, x0), max(0, y0), min(page_w, x1), min(page_h, y1))
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), clip=clip)
            img_bytes = pix.tobytes("png")
            try:
                from rapidocr_onnxruntime import RapidOCR
                ocr = RapidOCR()
                result, _ = ocr(img_bytes)
                if result:
                    authority = result[0][1].strip()
                else:
                    doc.close()
                    return []
            except Exception:
                doc.close()
                return []

        doc.close()
        doc_type_suffixes = (
            "任免通知", "文件", "通告", "公告", "决定", "命令", "批复",
            "意见", "纪要", "请示", "报告", "通报", "通知", "函", "令",
        )
        for suf in doc_type_suffixes:
            if authority.endswith(suf) and len(authority) > len(suf) + 2:
                authority = authority[: -len(suf)]
                break
        return [authority]
    except Exception:
        return []


def detect_issuing_authority_from_image_blocks(blocks: list) -> str:
    """从Qwen-VL识别的blocks中提取发文主体（red_header区域最靠上的行）。"""
    red_blocks = [b for b in blocks if b.get("region") == "red_header"]
    if not red_blocks:
        # 兜底：找颜色包含red、y_pct最小的块
        red_blocks = [b for b in blocks if "red" in str(b.get("color", "")).lower()]
    if not red_blocks:
        return ""
    red_blocks.sort(key=lambda b: b.get("y_pct", 1.0))
    text = red_blocks[0].get("text", "").strip()
    for suffix in ("文件", "函", "令"):
        if text.endswith(suffix) and len(text) > len(suffix) + 2:
            text = text[: -len(suffix)]
    return text


# 入口
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="格式保留本地化：在原图/PDF上直接覆盖替换省份、机关等短词",
    )
    parser.add_argument("--input", required=True, action="append", dest="inputs",
                        help="输入文件（图片或PDF），可重复指定")
    parser.add_argument("--target-authority", default=None,
                        help="目标发文机关全称（可选，不填则自动从原文机关名推导）")
    parser.add_argument("--target-province", required=True,
                        help="目标省份名称")
    parser.add_argument("--config", help="配置文件路径（config.json）")
    parser.add_argument("--output-dir", help="输出目录，默认与输入文件同目录")
    parser.add_argument("--font-dir", help="额外字体目录（优先于系统字体）")
    parser.add_argument("--no-preview", action="store_true", help="跳过PDF预览PNG生成")
    parser.add_argument("--skip-vision", action="store_true",
                        help="跳过Qwen-VL，复用输出目录中已有的 _blocks.json 缓存")
    parser.add_argument("--replacements-json",
                        help="可选显式替换JSON对象，键为原文，值为目标文字；显式映射优先")
    args = parser.parse_args()

    explicit_mapping = {}
    if args.replacements_json:
        replacements_path = Path(args.replacements_json).expanduser().resolve()
        loaded_mapping = json.loads(replacements_path.read_text(encoding="utf-8-sig"))
        if not isinstance(loaded_mapping, dict):
            raise ValueError("--replacements-json 必须包含JSON对象")
        explicit_mapping = {
            str(key): str(value)
            for key, value in loaded_mapping.items()
            if str(key).strip() and str(value).strip()
        }

    config_path = Path(args.config).expanduser() if args.config else None
    # 未指定config则尝试从兄弟目录找gov-doc-generator的config
    if not config_path:
        candidate = Path(__file__).resolve().parent.parent.parent / "gov-doc-generator" / "config.json"
        if candidate.exists():
            config_path = candidate
            print(f"  使用配置: {config_path}")

    config = load_config(config_path)
    font_dir = Path(args.font_dir) if args.font_dir else None

    for input_str in args.inputs:
        input_path = Path(input_str).expanduser().resolve()
        if not input_path.exists():
            print(f"错误: 文件不存在: {input_path}", file=sys.stderr)
            continue

        output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path.home() / "Desktop"
        output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'='*50}")
        print(f"格式保留本地化")
        print(f"输入: {input_path.name}")
        # 在 main 层面也做 shorten，供目标机关推导使用
        target_province_short = shorten_province(args.target_province)
        print(f"目标省份: {target_province_short}")
        print(f"输出目录: {output_dir}")

        # 识别发文主体
        suffix = input_path.suffix.lower()
        src_authorities = []
        if suffix == ".pdf":
            src_authorities = detect_issuing_authority_from_pdf(input_path, config)
            if src_authorities:
                print(f"发文主体: {'、'.join(src_authorities)}")
            else:
                print(f"发文主体: 未识别（文字层无红色文字）")

        # 推导目标机关名：若用户未指定，则只把省市裸名替换为目标省份裸名，后缀不动
        tgt_authority = args.target_authority
        if not tgt_authority:
            if src_authorities:
                import re as _re_auth
                first_src = src_authorities[0]
                stripped = _re_auth.sub(r'^中共', '', first_src)
                tgt_bare = _re_auth.sub(r'(?:省|市|自治区|自治州)$', '', target_province_short)
                # 先尝试标准省/市/自治区格式（如"南京市"、"广东省"）
                # 用非贪婪 {2,6}? 避免"天津市市场..."误匹配成"天津市市"
                replaced, n = _re_auth.subn(
                    r'^[\u4e00-\u9fff]{2,6}?(?:自治区|自治州|省|市)(?!场|局|委|厅|院|所|办)',
                    target_province_short, stripped, count=1
                )
                if n == 0:
                    # 标准格式匹不上，找开头匹配的省市裸名（如"重庆"在"重庆高新区..."）
                    _KNOWN_BARE = [
                        '内蒙古', '黑龙江', '广东', '江苏', '浙江', '山东', '河南', '湖北',
                        '湖南', '四川', '陕西', '辽宁', '河北', '山西', '吉林', '安徽',
                        '江西', '贵州', '云南', '甘肃', '青海', '广西', '海南', '西藏',
                        '新疆', '宁夏', '上海', '北京', '天津', '重庆', '福建',
                        '南京', '杭州', '武汉', '成都', '西安', '沈阳', '哈尔滨',
                        '济南', '青岛', '厦门', '深圳', '广州', '长沙', '郑州',
                    ]
                    for bare in sorted(_KNOWN_BARE, key=lambda x: -len(x)):
                        if stripped.startswith(bare):
                            replaced = tgt_bare + stripped[len(bare):]
                            n = 1
                            break
                if n:
                    tgt_authority = replaced
                else:
                    tgt_authority = target_province_short
                print(f"目标机关（自动推导）: {tgt_authority}")
            else:
                tgt_authority = target_province_short
                print(f"目标机关（兜底）: {tgt_authority}")

        print(f"{'='*50}")

        try:
            if suffix in IMAGE_SUFFIXES:
                localize_image(input_path, target_province_short, tgt_authority,
                               config, output_dir, font_dir, skip_vision=args.skip_vision,
                               explicit_mapping=explicit_mapping)
            elif suffix == ".pdf":
                localize_pdf(input_path, target_province_short, tgt_authority,
                             config, output_dir, font_dir, args.no_preview,
                             src_authorities=src_authorities,
                             explicit_mapping=explicit_mapping)
            else:
                print(f"  不支持的文件格式: {suffix}", file=sys.stderr)
        except Exception as e:
            print(f"  处理失败: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
