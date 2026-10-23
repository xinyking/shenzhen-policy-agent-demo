"""Explainable multi-domain routing; scores are matches, not calibrated probabilities."""
DOMAINS = {
 'residency': ('居住与户政', ['居住证','签注','居住登记','户政','户籍','入户']),
 'employment': ('就业与人才', ['就业','毕业','人才','求职','技能','见习','失业','入职','找工作','招聘','招募','双选','实习','稳岗']),
 'housing': ('住房与公积金', ['住房','租房','公租','安居','公积金','购房']),
 'insurance': ('社保与医保', ['社保','社会保险','医保','医疗保险','参保','异地就医','看病','少儿医保','医疗转移','断缴']),
 'family': ('生育与家庭', ['育儿','生育','托育','生孩']),
 'education': ('教育与学生资助', ['教育','入学','助学','学生资助','学费']),
 'care': ('养老与社会救助', ['养老','高龄','津贴','适老化','低保','救助','残疾','困难家庭']),
 'consumption': ('消费补贴', ['家电','数码','消费','智能家居','购新']),
 'enterprise': ('企业与产业扶持', ['企业','创业','产业','知识产权','科技项目']),
}
def route(text):
    return {'method': 'keyword_rules', 'domains': [
        {'id': key, 'name': name, 'matched_terms': [word for word in words if word in text]}
        for key, (name, words) in DOMAINS.items() if any(word in text for word in words)],
        'unknown': not any(word in text for _, words in DOMAINS.values() for word in words)}

def document_domains(doc):
    return [d['id'] for d in route(doc['title'] + ' ' + doc.get('topic', ''))['domains']]
