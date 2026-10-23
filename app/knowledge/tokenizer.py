"""Chinese bigram lexical tokens for document retrieval."""
import re


def tokens(text):
    result = set(re.findall(r'[a-z0-9_]+', text.lower()))
    for run in re.findall(r'[\u4e00-\u9fff]+', text):
        if len(run) == 1:
            result.add(run)
        result.update(run[index:index + 2] for index in range(len(run) - 1))
    return result
