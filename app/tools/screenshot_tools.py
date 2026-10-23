"""Bounded in-memory uploads and evidence-separated screenshot material contracts."""
from app.agent.prompts import READ_SCREENSHOT, COMPARE_MATERIALS
import io
import re
import time
from datetime import date
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from app.agent.llm import ModelError

MAX_IMAGE_BYTES = 4 * 1024 * 1024


class ScreenshotError(ValueError):
    pass


def validate_image(raw, mime):
    if mime not in ('image/png', 'image/jpeg') or not 0 < len(raw) <= MAX_IMAGE_BYTES:
        raise ScreenshotError('仅支持不超过4MiB的PNG/JPEG截图')
    try:
        with Image.open(io.BytesIO(raw), formats=['PNG', 'JPEG']) as image:
            if image.width * image.height > 12_000_000 or min(image.size) < 32:
                raise ScreenshotError('截图尺寸须至少32像素，且不超过1200万像素')
            actual = 'image/png' if image.format == 'PNG' else 'image/jpeg'
            if actual != mime or getattr(image, 'is_animated', False):
                raise ScreenshotError('图片格式与上传类型不一致，或为动态图')
            size = image.size
            image.verify()
        return {'width': size[0], 'height': size[1], 'mime': actual}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, SyntaxError):
        raise ScreenshotError('截图无法解码，请重新保存为PNG或JPEG') from None


class ScreenshotStore:
    """Raw pixels never enter the task journal. At most 8 pending uploads for 10 minutes."""
    def __init__(self):
        self.items = {}

    def cleanup(self):
        expired = [key for key, item in self.items.items() if time.monotonic() - item['created'] > 600]
        for key in expired:
            self.items.pop(key, None)

    def put(self, raw, info):
        self.cleanup()
        if len(self.items) >= 8:
            raise ScreenshotError('待处理截图较多，请先处理已有截图或稍后重试')
        key = uuid4().hex
        self.items[key] = {'raw': raw, **info, 'created': time.monotonic()}
        return {'screenshot_id': key, **info, 'expires_in_seconds': 600}

    def take(self, key):
        self.cleanup()
        if key not in self.items:
            raise ScreenshotError('截图已使用、过期或后端已重启，请重新上传')
        return self.items.pop(key)




def text(value, limit, empty=True):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ModelError('截图识别字段格式无效')
    return value.strip()


def validate_recognition(value):
    keys = {'title', 'is_application_page', 'visible_text', 'materials', 'procedure', 'dates', 'gaps'}
    if not isinstance(value, dict) or set(value) != keys or type(value['is_application_page']) is not bool:
        raise ModelError('截图识别输出格式无效')
    title, visible = text(value['title'], 150), text(value['visible_text'], 8000)
    if not isinstance(value['materials'], list) or len(value['materials']) > 16:
        raise ModelError('截图材料列表格式无效')
    materials = []
    for i, item in enumerate(value['materials'], 1):
        if not isinstance(item, dict) or set(item) != {'name', 'requirement', 'condition', 'quote', 'location'}:
            raise ModelError('截图材料字段无效')
        if item['requirement'] not in ('required', 'conditional', 'optional', 'unclear'):
            raise ModelError('截图材料要求类型无效')
        row = {'id': f'S{i}', 'name': text(item['name'], 150, False),
               'requirement': item['requirement'], 'condition': text(item['condition'], 300),
               'quote': text(item['quote'], 500, False), 'location': text(item['location'], 100, False)}
        if row['quote'] not in visible:
            raise ModelError('材料摘录不在识别文字中')
        materials.append(row)
    if not value['is_application_page'] and materials:
        raise ModelError('非办事页面不能生成材料要求')
    if not isinstance(value['dates'], list) or len(value['dates']) > 4:
        raise ModelError('截图日期格式无效')
    dates = []
    for item in value['dates']:
        if not isinstance(item, dict) or set(item) != {'date', 'label', 'quote', 'location'}:
            raise ModelError('截图日期字段无效')
        try:
            parsed = date.fromisoformat(item['date'])
        except (ValueError, TypeError):
            raise ModelError('截图日期无法确认') from None
        quote = text(item['quote'], 500, False)
        # Require an actual complete date in the recognized excerpt, not a guessed year.
        found = re.findall(r'(\d{4})\s*[-年/.]\s*(\d{1,2})\s*[-月/.]\s*(\d{1,2})', quote)
        if item['date'] != parsed.isoformat() or quote not in visible or not any(tuple(map(int, p)) == (parsed.year, parsed.month, parsed.day) for p in found):
            raise ModelError('日期缺少可见年月日依据')
        dates.append({'date': parsed.isoformat(), 'label': text(item['label'], 100, False),
                      'quote': quote, 'location': text(item['location'], 100, False)})
    if not isinstance(value['gaps'], list) or len(value['gaps']) > 5:
        raise ModelError('截图缺口格式无效')
    return {'status': 'recognized', 'title': title, 'is_application_page': value['is_application_page'],
            'materials': materials, 'procedure': text(value['procedure'], 1000), 'dates': dates,
            'gaps': [text(g, 200, False) for g in value['gaps']], 'recognition_requires_confirmation': True}




def validate_comparison(value, materials, evidence):
    if not isinstance(value, dict) or set(value) != {'checks'} or not isinstance(value['checks'], list) or len(value['checks']) != len(materials):
        raise ModelError('材料对照输出格式无效')
    by_id = {e['evidence_id']: e for e in evidence}
    allowed, checks = {m['id'] for m in materials}, {}
    for item in value['checks']:
        if not isinstance(item, dict) or set(item) != {'id', 'verdict', 'policy_evidence_ids', 'policy_quote', 'explanation'}:
            raise ModelError('材料对照字段无效')
        key, verdict, ids = item['id'], item['verdict'], item['policy_evidence_ids']
        if not isinstance(key, str) or key not in allowed or key in checks or verdict not in ('supported', 'conflict', 'not_found'):
            raise ModelError('材料对照身份或状态无效')
        if not isinstance(ids, list) or len(ids) > 3 or not all(isinstance(i, str) and i in by_id for i in ids) or len(set(ids)) != len(ids):
            raise ModelError('材料对照引用无效')
        quote = text(item['policy_quote'], 600)
        if verdict == 'not_found':
            if ids or quote:
                raise ModelError('未查到依据不能声称已有引用')
        elif not ids or not quote or not any(quote in by_id[i]['text'] for i in ids):
            raise ModelError('材料政策摘录不匹配')
        checks[key] = {**item, 'explanation': text(item['explanation'], 200, False)}
    return checks
