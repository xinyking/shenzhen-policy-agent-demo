"""Minimal optional clarification; source gaps are not user eligibility gaps."""
import re

FIELDS = {'employment': ['毕业时间', '就业状态', '深圳社保'], 'housing': ['在深住房', '住房支持', '公积金'], 'insurance': ['医保类型', '就医地点']}
KNOWN = {'毕业时间': ['毕业2年内', '毕业超过2年', '在读'], '就业状态': ['待业', '已入职', '灵活就业', '就业见习'],
         '深圳社保': ['未缴纳', '缴纳不足3个月', '连续缴纳满3个月'], '在深住房': ['无自有住房', '有自有住房'],
         '住房支持': ['未享受住房优惠', '正在享受住房优惠'], '公积金': ['正在缴存', '未缴存'],
         '医保类型': ['职工医保', '居民医保', '未参保'], '就医地点': ['深圳市内', '深圳市外', '尚未就医']}


def follow_up_fields(routing, context):
    keys = list(dict.fromkeys(key for domain in routing.get('domains', []) for key in FIELDS.get(domain['id'], [])))
    return [key for key in keys if key not in context and not any(word in context for word in KNOWN[key])][:2]


def build_follow_up(routing, context, model_question=''):
    keys = follow_up_fields(routing, context)
    if not keys:
        return None
    if (not model_question.strip() or not all(key in model_question for key in keys)
            or re.search(r'身份证|证件号|姓名|手机号|电话号码|银行卡|住址', model_question)):
        model_question = '如果你愿意结合自己的情况进一步了解，可以补充' + '、'.join(keys) + '。下面点选即可，也可以直接回复；不填写仍可继续查询。'
    return {'question': model_question, 'fields': keys}
