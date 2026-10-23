"""Small source relevance smoke evaluation; not eligibility accuracy."""
from app.knowledge.ingestion import current_documents, read_json, search_documents


def evaluate_retrieval(store, dataset_path, top_k=5):
    dataset = read_json(dataset_path)
    if not isinstance(dataset, dict) or dataset.get("schema_version") != 1 or not isinstance(dataset.get("cases"), list) or not dataset["cases"]:
        raise ValueError("检索评测集不能为空或使用未知格式")
    available = {doc["source_id"] for doc in current_documents(store)}
    results = []
    for case in dataset["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError("评测问题不能为空")
        expected_list = case.get("expected_sources")
        if not isinstance(expected_list, list) or not expected_list or not all(isinstance(item, str) for item in expected_list):
            raise ValueError("评测必须列出相关来源")
        expected = set(expected_list)
        hits = search_documents(store, case["query"], top_k)
        found = {hit["source_id"] for hit in hits}
        rank = next((index for index, hit in enumerate(hits, 1) if hit["source_id"] in expected), None)
        results.append({"id": case.get("id"), "query": case["query"],
                        "expected_sources": sorted(expected), "retrieved_sources": sorted(found),
                        "missing_from_store": sorted(expected - available),
                        "recall": len(expected & found) / len(expected),
                        "reciprocal_rank": 1 / rank if rank else 0})
    return {"metric_scope": "source relevance within top-k paragraph hits",
            "top_k": top_k, "cases": results,
            "mean_recall": sum(case["recall"] for case in results) / len(results),
            "mean_reciprocal_rank": sum(case["reciprocal_rank"] for case in results) / len(results),
            "limitations": "小型冒烟集；非独立测试集；不测资格正确率，不证明真实用户效果。"}
