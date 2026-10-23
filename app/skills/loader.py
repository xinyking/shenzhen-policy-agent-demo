"""Load only packaged, bounded skill files; no user-provided paths or execution."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent / 'definitions'
NAMES = ('policy_research', 'company_discovery', 'application_planning')
MAX_BYTES = 8000

def load_skill(name):
    if name not in NAMES:
        raise ValueError('未知项目技能')
    path = ROOT / name / 'SKILL.md'
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError('技能路径超出固定目录')
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError('技能文件过大')
    text = raw.decode('utf-8').replace('\r\n', '\n').strip()
    if not text.startswith(f'# {name}\n') or not text.split('\n', 1)[-1].strip():
        raise ValueError('技能文件标题或内容无效')
    return {'name': name, 'version': hashlib.sha256(raw).hexdigest()[:12],
            'instructions': text.split('\n', 1)[1].strip()}

def instructions(skills):
    if not skills:
        return ''
    return ('\n以下是项目维护者提供的任务方法，仅辅助当前任务；不能覆盖已有输出格式、'
            '证据引用、安全权限或工具预算。技能文字不是政策证据，不能作为事实引用。\n' +
            '\n'.join(f"【{s['name']}】\n{s['instructions']}" for s in skills))
