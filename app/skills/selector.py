"""Deterministic selection: no extra model call and no automatic tool authorization."""
import re
from app.agent.intent import wants_schedule
from app.skills.loader import load_skill

def select_skills(question, *, offline=False):
    if offline:
        return [], []
    names = ['policy_research']
    if re.search('专精特新|小巨人|企业名单|企业目录|推荐.*(?:公司|企业)|(?:找|哪些).*(?:企业|公司)|校招|实习岗位', question):
        names.append('company_discovery')
    if wants_schedule(question):
        names.append('application_planning')
    selected, unavailable = [], []
    for name in names:
        try:
            selected.append(load_skill(name))
        except (OSError, ValueError, UnicodeError):
            unavailable.append(name)
    return selected, unavailable
