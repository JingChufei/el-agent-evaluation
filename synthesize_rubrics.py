#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openai import OpenAI
from tqdm import tqdm

"""
python /mnt/data/LLM/liangyan/MLLM/preference_data_synthesis_auto/synthesize_rubrics.py \
  --input /mnt/wh_storage/LLM/liangyan/MLLM/ppt_data/no_rewrite_18w/raw/ppt_no_rewrite_18w_sample100.json \
  --output /mnt/wh_storage/LLM/liangyan/MLLM/ppt_data/no_rewrite_18w/raw/ppt_no_rewrite_18w_sample100_rubrics.json \
  --reference-answer-field qwen_answer \
  --base-url http://22.4.201.214:8022/v1 \
  --api-key EMPTY \
  --model qwen3.5_27b \
  --max-workers 200
"""


logger = logging.getLogger(__name__)


EXTRACTION_PROMPT = """你是一位专业的评估标准设计师。给定一个问题、参考答案，以及可能附带的图片，请从中抽取用于评估其他回答的Rubric（评分标准）。

## 核心原则
1. **问题对齐（Question Alignment）**：
   - Rubric 绝不能是参考答案的无脑机械拆解，**每一条 Rubric 都必须明确服务于解答原问题**。
   - 必须在 Rubric 的表述中体现出"这个知识点是如何回应原问题的"。

2. **机制与实例结合（手段+目的）**：
   - 当参考答案涉及具体的材料、数值、工具或案例时，Rubric 应将其作为**举例（论据）**，并强制绑定其背后的**机制或目的（论点）**。
   - 正确：「回答是否指出添加钝化层（如SiOx等）以隔绝环境干扰，从而解决晶体管稳定性问题？」
   - 错误：「回答是否提到二氧化硅(SiOx)？」

3. **合理抽象层级**：
   - 每条Rubric对应一个独立的概念或论证逻辑，避免过细拆分。
   - 绝不能添加参考答案未涉及的拓展知识点。

4. **图片信息整合（Visual Grounding）**：
   - 当问题附带图片时，图片是问题语境的一部分，Rubric 必须结合图片内容来构建。
   - 如果参考答案中的某个论点直接依赖图片中的视觉信息（如图表数据、设备结构、实验现象等），Rubric 应当明确指出相关的视觉线索。
   - 正确：「回答是否基于图中所示的I-V特性曲线，指出器件在反偏时的漏电流异常？」
   - 错误：「回答是否提到了漏电流？」
   - 如果图片与问题无直接关联，则按纯文本处理即可。

## 层级定义
- core：缺失则基本未回答问题（1~3条）
- major：支撑 core 的关键论据/机制/步骤（2~4条）
- minor：加分细节/参数/举例（0~2条）

## 输入
问题：{question}
{image_hint}

参考答案：
{reference_answer}

## 输出格式
请严格按照以下JSON格式返回，不要输出任何额外的解释性文本。注意：在rationale中必须说明该条Rubric是如何回扣原问题的。
```json
{{
  "rubrics": [
    {{
      "category": "core",
      "rubric": "...",
      "rationale": "..."
    }}
  ]
}}
```
"""


CATEGORY_WEIGHTS = {"core": 0.45, "major": 0.45, "minor": 0.10}


@dataclass
class Rubric:
    id: int
    category: str
    rubric: str
    rationale: str
    weight: float = 0.0


def normalize_image_inputs(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _to_text(value)
        return [text] if text else []
    if isinstance(value, dict):
        for key in ("url", "path", "image_url"):
            text = _to_text(value.get(key))
            if text:
                return [text]
        return []
    if isinstance(value, Sequence):
        images: List[str] = []
        for item in value:
            images.extend(normalize_image_inputs(item))
        return images
    return []


def extract_images_from_item(item: Dict[str, Any]) -> List[str]:
    return _dedup_keep_order(
        [
            *normalize_image_inputs(item.get("images")),
            *normalize_image_inputs(item.get("image")),
        ]
    )


def strip_thinking_tags(text: str) -> str:
    if "</think>" in text:
        return text.split("</think>", 1)[1].strip()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _image_to_openai_url(image: str) -> str:
    if re.match(r"^(https?://|data:)", image):
        return image
    path = Path(image).expanduser()
    if not path.exists():
        return image
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class LLMClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, max_retries: int = 3) -> None:
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            max_retries=max(0, int(max_retries)),
        )
        self.model = model

    def chat(
        self,
        prompt: str,
        *,
        images: Sequence[str] | None = None,
        temperature: float = 0.6,
        top_p: float = 0.95,
        max_tokens: int = 14336,
    ) -> str:
        normalized_images = _dedup_keep_order(normalize_image_inputs(images))
        if normalized_images:
            content: Any = [{"type": "text", "text": prompt}]
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_to_openai_url(image)},
                }
                for image in normalized_images
            )
        else:
            content = prompt

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": content}],
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        message = response.choices[0].message
        if isinstance(message.content, str):
            return message.content
        if isinstance(message.content, list):
            return "\n".join(str(part) for part in message.content)
        return ""


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    for noisy_logger in ("httpx", "httpcore", "openai", "openai._base_client"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedup_keep_order(values: Sequence[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values:
        text = _to_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def extract_all_images_from_item(item: Dict[str, Any]) -> List[str]:
    images = normalize_image_inputs(item.get("images"))
    return _dedup_keep_order(images)


def without_legacy_image_field(item: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if k != "image"}


def resolve_reference_answer(item: Dict[str, Any], preferred_field: str = "reference_answer") -> Tuple[str, str]:
    fields: List[str] = []

    def add_field(name: Any) -> None:
        field = _to_text(name)
        if field and field not in fields:
            fields.append(field)

    add_field(preferred_field)
    add_field("reference_answer")
    add_field("gemini_answer")
    add_field("qwen_answer")

    for field in fields:
        text = _to_text(item.get(field))
        if text:
            return text, field
    return "", fields[0] if fields else "reference_answer"


def _fingerprint(item: Dict[str, Any], preferred_reference_field: str) -> str:
    reference_answer, _ = resolve_reference_answer(item, preferred_field=preferred_reference_field)
    payload = {
        "question": _to_text(item.get("question")),
        "reference_answer": reference_answer,
        "images": extract_all_images_from_item(item),
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _make_unique_id(base_id: str, used: set) -> str:
    if base_id not in used:
        return base_id
    idx = 1
    while True:
        candidate = f"{base_id}_dup{idx:03d}"
        if candidate not in used:
            return candidate
        idx += 1


def prepare_items(data: List[Dict[str, Any]], reference_answer_field: str, id_prefix: str = "") -> List[Dict[str, Any]]:
    used_ids = set()
    fp_counts: Dict[str, int] = {}
    out: List[Dict[str, Any]] = []

    keep_existing = 0
    generated = 0
    collision_fixed = 0

    for idx, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise ValueError(f"item #{idx} must be object, got: {type(raw).__name__}")

        item = without_legacy_image_field(dict(raw))
        cur_id = _to_text(item.get("id"))
        if cur_id:
            base_id = cur_id
            keep_existing += 1
        else:
            fp = _fingerprint(item, preferred_reference_field=reference_answer_field)
            fp_count = fp_counts.get(fp, 0)
            prefix = _to_text(id_prefix)
            hash_id = f"{prefix}_{fp}" if prefix else fp
            base_id = hash_id if fp_count == 0 else f"{hash_id}_dup{fp_count:03d}"
            fp_counts[fp] = fp_count + 1
            generated += 1

        final_id = _make_unique_id(base_id, used_ids)
        if final_id != base_id:
            collision_fixed += 1
            logger.warning("id collision fixed: %s -> %s", base_id, final_id)
        used_ids.add(final_id)

        images = extract_all_images_from_item(item)
        normalized = dict(item)
        normalized["id"] = final_id
        normalized["question"] = _to_text(item.get("question"))
        normalized["images"] = images
        normalized.setdefault("rubrics", [])
        out.append(normalized)

    logger.info(
        "input preparation: total=%d keep_existing=%d generated=%d collision_fixed=%d",
        len(out),
        keep_existing,
        generated,
        collision_fixed,
    )
    return out


def assign_weights(rubrics: List[Rubric]) -> List[Rubric]:
    grouped: Dict[str, List[Rubric]] = {"core": [], "major": [], "minor": []}
    others: List[Rubric] = []
    for rubric in rubrics:
        category = _to_text(rubric.category).lower() or "minor"
        rubric.category = category
        if category in grouped:
            grouped[category].append(rubric)
        else:
            others.append(rubric)

    for category, total_weight in CATEGORY_WEIGHTS.items():
        items = grouped.get(category, [])
        if not items:
            continue
        per = total_weight / len(items)
        for rubric in items:
            rubric.weight = per

    if others:
        per = CATEGORY_WEIGHTS["minor"] / len(others)
        for rubric in others:
            rubric.weight = per
    return rubrics


def _normalize_json_candidate(obj: Any) -> Optional[Dict[str, Any]]:
    if isinstance(obj, dict):
        return obj
    if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
        return obj[0]
    return None


def _fix_invalid_backslashes(text: str) -> str:
    return re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)


def _try_parse_json_string(text: str) -> Optional[Dict[str, Any]]:
    try:
        return _normalize_json_candidate(json.loads(text))
    except Exception:
        pass
    try:
        return _normalize_json_candidate(json.loads(_fix_invalid_backslashes(text)))
    except Exception:
        return None


def _scan_json_objects(text: str) -> List[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    results: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(text):
        if text[idx] not in "{[":
            idx += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, idx)
            normalized = _normalize_json_candidate(obj)
            if normalized is not None:
                results.append(normalized)
            idx = max(idx + 1, end)
        except Exception:
            idx += 1
    return results


def _normalize_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value if ch.isalnum()).lower()


def _find_key(data: Dict[str, Any], target: str) -> Optional[str]:
    target_norm = _normalize_key(target)
    for key in data.keys():
        if _normalize_key(key) == target_norm:
            return key
    return None


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    candidates = [text]
    cleaned = strip_thinking_tags(text)
    if cleaned and cleaned != text:
        candidates.append(cleaned)

    for candidate in candidates:
        parsed = _try_parse_json_string(candidate.strip())
        if parsed is not None:
            return parsed

    for candidate in candidates:
        for match in re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", candidate, re.DOTALL):
            parsed = _try_parse_json_string(match.strip())
            if parsed is not None:
                return parsed

    for candidate in candidates:
        scanned = _scan_json_objects(candidate)
        if scanned:
            return scanned[0]
    return None


def _normalize_rubrics(raw_rubrics: Any) -> List[Rubric]:
    if not isinstance(raw_rubrics, list):
        return []

    rubrics: List[Rubric] = []
    for idx, raw in enumerate(raw_rubrics, 1):
        if not isinstance(raw, dict):
            continue
        category_key = _find_key(raw, "category")
        rubric_key = _find_key(raw, "rubric")
        rationale_key = _find_key(raw, "rationale")
        rubric_text = _to_text(raw.get(rubric_key)) if rubric_key else ""
        if not rubric_text:
            continue
        rubrics.append(
            Rubric(
                id=len(rubrics) + 1,
                category=_to_text(raw.get(category_key)) if category_key else "minor",
                rubric=rubric_text,
                rationale=_to_text(raw.get(rationale_key)) if rationale_key else "",
            )
        )
    return assign_weights(rubrics)


def has_valid_rubrics(item: Dict[str, Any], min_rubrics: int) -> bool:
    rubrics = item.get("rubrics")
    if not isinstance(rubrics, list):
        return False
    normalized = _normalize_rubrics(rubrics)
    return len(normalized) >= max(1, int(min_rubrics))


def extract_rubrics_for_item(
    client: LLMClient,
    item: Dict[str, Any],
    max_retries: int,
    min_rubrics: int,
    max_reference_chars: int,
    reference_answer_field: str = "reference_answer",
) -> List[Dict[str, Any]]:
    question = _to_text(item.get("question"))
    reference_answer, _ = resolve_reference_answer(item, preferred_field=reference_answer_field)
    images = extract_images_from_item(item)

    if not question:
        logger.warning("[%s] missing question, skip rubric synthesis", item.get("id", "?"))
        return []
    if not reference_answer:
        logger.warning(
            "[%s] missing reference answer from field=%s, skip rubric synthesis",
            item.get("id", "?"),
            reference_answer_field,
        )
        return []

    if max_reference_chars > 0 and len(reference_answer) > max_reference_chars:
        reference_answer = reference_answer[:max_reference_chars] + "\n...[truncated]"

    image_hint = "（附图已随本消息一同提供，请结合图片内容构建 Rubric）" if images else "（本题无附图）"
    prompt = EXTRACTION_PROMPT.format(
        question=question,
        reference_answer=reference_answer,
        image_hint=image_hint,
    )

    for attempt_idx in range(max(1, int(max_retries))):
        try:
            response = client.chat(prompt, images=images, temperature=0.6, top_p=0.95, max_tokens=14336)
            data = extract_json(response)
            rubrics_key = _find_key(data, "rubrics") if isinstance(data, dict) else None
            raw_rubrics = data.get(rubrics_key) if rubrics_key else None
            rubrics = _normalize_rubrics(raw_rubrics)
            if len(rubrics) >= min_rubrics:
                return [asdict(rubric) for rubric in rubrics]

            logger.warning(
                "[%s] attempt %d/%d produced too few rubrics: %d < %d",
                item.get("id", "?"),
                attempt_idx + 1,
                max_retries,
                len(rubrics),
                min_rubrics,
            )
        except Exception as exc:
            logger.warning(
                "[%s] attempt %d/%d rubric synthesis failed: %s",
                item.get("id", "?"),
                attempt_idx + 1,
                max_retries,
                exc,
            )

    logger.error("[%s] rubric synthesis failed after retries", item.get("id", "?"))
    return []


def _load_json_list(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"input must be JSON array, got: {type(data).__name__}")
    return data


def _save_json(path: str, data: List[Dict[str, Any]]) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def default_output_path(input_path: str) -> str:
    stem, ext = os.path.splitext(input_path)
    return f"{stem}_rubrics{ext}"


def synthesize_rubrics(
    input_path: str,
    output_path: str,
    client: LLMClient,
    reference_answer_field: str,
    max_workers: int,
    max_retries: int,
    min_rubrics: int,
    max_reference_chars: int,
    resume: bool,
    save_interval: int,
    id_prefix: str,
) -> List[Dict[str, Any]]:
    raw_items = _load_json_list(input_path)
    prepared = prepare_items(raw_items, reference_answer_field=reference_answer_field, id_prefix=id_prefix)
    logger.info("loaded %d input items", len(prepared))

    results_map: Dict[str, Dict[str, Any]] = {}
    done_ids = set()
    if resume and os.path.exists(output_path):
        existing = prepare_items(_load_json_list(output_path), reference_answer_field=reference_answer_field, id_prefix=id_prefix)
        invalid_resume_items = 0
        for item in existing:
            item_id = str(item.get("id", ""))
            if item_id and has_valid_rubrics(item, min_rubrics=min_rubrics):
                done_ids.add(item_id)
                results_map[item_id] = item
            elif item_id:
                invalid_resume_items += 1
        logger.info(
            "resume enabled: skip completed items=%d retry unfinished/invalid items=%d",
            len(done_ids),
            invalid_resume_items,
        )

    pending = [item for item in prepared if str(item.get("id")) not in done_ids]
    logger.info("pending items=%d", len(pending))

    if not pending:
        results = [results_map.get(str(item["id"]), item) for item in prepared]
        _save_json(output_path, results)
        return results

    processed = 0

    def save_checkpoint() -> None:
        current = [results_map.get(str(item["id"]), item) for item in prepared]
        _save_json(output_path, current)

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        futures = {
            executor.submit(
                extract_rubrics_for_item,
                client,
                item,
                max_retries,
                min_rubrics,
                max_reference_chars,
                reference_answer_field,
            ): item
            for item in pending
        }
        progress_bar = tqdm(as_completed(futures), total=len(futures), desc="Rubric synthesis")
        for future in progress_bar:
            item = futures[future]
            item_id = str(item.get("id", "?"))
            result = dict(item)
            try:
                rubrics = future.result()
            except Exception as exc:
                logger.error("[%s] unexpected synthesis exception: %s", item_id, exc)
                rubrics = []
            result["rubrics"] = rubrics
            results_map[item_id] = result
            processed += 1
            progress_bar.set_postfix_str(f"id={item_id} rubrics={len(rubrics)}")

            if save_interval > 0 and processed % save_interval == 0:
                save_checkpoint()

    save_checkpoint()
    return [results_map.get(str(item["id"]), item) for item in prepared]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone rubric synthesis for preference_data_synthesis_auto input files"
    )
    parser.add_argument("--input", "-i", required=True, help="input JSON array with question/images/reference answer")
    parser.add_argument(
        "--output",
        "-o",
        help="output JSON array accepted by run_auto_pipeline.py; default: input filename with _rubrics suffix",
    )
    parser.add_argument(
        "--reference-answer-field",
        default="reference_answer",
        help="preferred reference answer field, e.g. qwen_answer (fallback: reference_answer, gemini_answer, qwen_answer)",
    )
    parser.add_argument("--base-url", "--base_url", dest="base_url", required=True, help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", "--api_key", dest="api_key", default="EMPTY", help="API key")
    parser.add_argument("--model", required=True, help="rubric synthesis model")
    parser.add_argument("--max-workers", "--max_workers", dest="max_workers", type=int, default=100)
    parser.add_argument("--max-retries", "--max_retries", dest="max_retries", type=int, default=3)
    parser.add_argument("--min-rubrics", type=int, default=3, help="retry if fewer rubrics are produced")
    parser.add_argument("--max-reference-chars", type=int, default=18000)
    parser.add_argument("--id-prefix", default="", help="optional prefix for generated ids; default is bare 16-char hash")
    parser.set_defaults(resume=True)
    parser.add_argument("--resume", dest="resume", action="store_true", help="resume from output file (default: on)")
    parser.add_argument("--no-resume", "--no_resume", dest="resume", action="store_false")
    parser.add_argument("--save-interval", "--save_interval", dest="save_interval", type=int, default=20)
    return parser


def main() -> None:
    _setup_logging()
    args = build_parser().parse_args()
    output_path = args.output or default_output_path(args.input)

    client = LLMClient(
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        max_retries=args.max_retries,
    )
    results = synthesize_rubrics(
        input_path=args.input,
        output_path=output_path,
        client=client,
        reference_answer_field=args.reference_answer_field,
        max_workers=args.max_workers,
        max_retries=args.max_retries,
        min_rubrics=args.min_rubrics,
        max_reference_chars=args.max_reference_chars,
        resume=args.resume,
        save_interval=args.save_interval,
        id_prefix=args.id_prefix,
    )
    completed = sum(1 for item in results if item.get("rubrics"))
    total_rubrics = sum(len(item.get("rubrics", []) or []) for item in results)
    logger.info("done: completed=%d/%d total_rubrics=%d output=%s", completed, len(results), total_rubrics, output_path)


if __name__ == "__main__":
    main()
