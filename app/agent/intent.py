"""Conservative schedule authorization and information-oriented query needs."""
import re

def personal_advice_requested(question):
    return bool(re.search(r'(我|本人).{0,10}(能否|能不能|是否符合|是否可以|有资格|适合|可以申请|能申请|能领)|根据我的|结合我的|针对我',question))

def asks_for_identity(text):
    return bool(re.search(r'(提供|填写|补充|告知|告诉|确认).{0,20}(年龄|学历|户籍|个人信息|身份信息|社保情况|就业状态|单位信息)',text))

def wants_schedule(question):
    if re.search(r'(不要|不用|无需|不必|不需要|别).{0,8}(日程|日历|提醒|计划)', question):
        return False
    return bool(re.search(r'(添加|加入|加到|创建|安排|生成|制定|规划|设置|记入|写入).{0,12}(日程|日历|提醒|计划)|提醒我|(日程|日历).{0,8}(添加|创建|安排|规划)', question))

def information_needs(question):
    needs = ['overview','audience']
    for words, name in [('条件|对象|人群','conditions'),('流程|如何|怎么|办理|申请|申报','procedure'),
                        ('材料|资料|附件','materials'),('最近|最新|时间|日期|截止|今年','dates'),('入口|渠道|哪里','channel')]:
        if re.search(words, question): needs.append(name)
    return needs
