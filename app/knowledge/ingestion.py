"""Allowlisted HTML collection, immutable snapshots and source-level search.

Collected documents are research evidence, not approved eligibility policies.
"""
import hashlib
import json
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from app.knowledge.tokenizer import tokens
from app.utils.storage_budget import ensure_capacity, StorageLimit, MAX_TOTAL_BYTES

USER_AGENT = "ShenzhenPolicyResearch/0.2"
MAX_BYTES = 4_000_000


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def document_version(document, source_url):
    content = {key: document[key] for key in
               ("title", "text", "published_at", "publisher", "attachments")}
    content["source_url"] = source_url
    return digest(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def validate_url(url, hosts):
    if not isinstance(url, str):
        raise ValueError("来源 URL 必须是字符串")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in hosts or
        parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("只允许白名单内的 HTTPS 官方来源")
    return url


def load_sources(path):
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("不支持的来源清单")
    hosts = payload.get("allowed_hosts")
    sources = payload.get("sources")
    if not isinstance(hosts, list) or not hosts or not all(
        isinstance(host, str) and (host == "sz.gov.cn" or host.endswith(".sz.gov.cn"))
        for host in hosts
    ):
        raise ValueError("来源白名单必须为深圳政府域名")
    if not isinstance(sources, list):
        raise ValueError("sources 必须是列表")
    ids = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("来源记录必须是对象")
        for field in ("id", "url", "selector", "kind", "topic", "status"):
            if not isinstance(source.get(field), str) or not source[field]:
                raise ValueError(f"来源缺少 {field}")
        if not re.fullmatch(r"[a-z0-9-]{1,80}", source["id"]) or source["id"] in ids:
            raise ValueError("来源 ID 非法或重复")
        if source["status"] not in {"pending", "archived"}:
            raise ValueError("来源状态仅支持 pending 或 archived")
        validate_url(source["url"], hosts)
        ids.add(source["id"])
    for source in sources:
        related = source.get("related_ids")
        if not isinstance(related, list) or not all(isinstance(item, str) and item in ids for item in related):
            raise ValueError("来源关联 ID 无效")
    return payload


class SafeRedirect(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl, self.hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OfficialFetcher:
    def __init__(self, hosts, tls_curve=None, interval=1.0, max_bytes=MAX_BYTES):
        context = ssl.create_default_context()
        if tls_curve:
            context.set_ecdh_curve(tls_curve)
        self.hosts = hosts
        if not 1 <= max_bytes <= 20_000_000:
            raise ValueError('单次下载上限必须在1至20MB内')
        self.max_bytes = max_bytes
        self.opener = build_opener(SafeRedirect(hosts), HTTPSHandler(context=context))
        self.interval = interval
        self.last_request = 0.0
        self.robots = {}

    def request(self, url):
        validate_url(url, self.hosts)
        delay = self.interval - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()
        request = Request(url, headers={"User-Agent": USER_AGENT})
        with self.opener.open(request, timeout=25) as response:
            validate_url(response.url, self.hosts)
            raw = response.read(self.max_bytes + 1)
            if len(raw) > self.max_bytes:
                raise ValueError("文档超过配置的大小限制")
            return raw, response.headers.get_content_type(), response.headers.get_content_charset()

    def fetch(self, url):
        parsed = urlparse(validate_url(url, self.hosts))
        origin = f"https://{parsed.netloc}"
        if origin not in self.robots:
            try:
                raw, _, _ = self.request(origin + "/robots.txt")
                parser = RobotFileParser()
                parser.parse(raw.decode("utf-8", errors="replace").splitlines())
                self.robots[origin] = parser
            except HTTPError as exc:
                if exc.code not in (404, 410):
                    raise ValueError(f"robots.txt 获取失败 HTTP {exc.code}，停止采集") from None
                self.robots[origin] = None
        parser = self.robots[origin]
        if parser and not parser.can_fetch(USER_AGENT, url):
            raise ValueError("robots.txt 禁止访问该来源")
        return self.request(url)


def parse_html(raw, source, charset=None):
    soup = BeautifulSoup(raw, "html.parser", from_encoding=charset)
    root = soup.select_one(source["selector"])
    if root is None:
        raise ValueError("未找到配置的正文选择器，拒绝使用全网页代替正文")
    meta = {item.get("name", "").lower(): item.get("content", "") for item in soup.find_all("meta")}
    title = meta.get("articletitle") or (soup.h1.get_text(" ", strip=True) if soup.h1 else "")
    if not title:
        raise ValueError("无法识别政策文档标题")
    attachments = []
    for anchor in root.select("a[href]"):
        url = urljoin(source["url"], anchor["href"])
        if urlparse(url).path.lower().endswith((".pdf", ".doc", ".docx", ".xls", ".xlsx")):
            attachments.append({"url": url, "title": anchor.get_text(" ", strip=True)})
    for element in root.select("script,style,noscript"):
        element.decompose()
    paragraphs = [re.sub(r"\s+", " ", part).strip() for part in root.get_text("\n", strip=True).splitlines()]
    paragraphs = [part for part in paragraphs if part]
    text = "\n".join(paragraphs)
    if len(text) < 20:
        raise ValueError("正文过短，可能为错误页或未加载内容")
    spans, offset, section = [], 0, "正文"
    for index, paragraph in enumerate(paragraphs, 1):
        match = re.match(r"(第[一二三四五六七八九十百零〇\d]+条)", paragraph)
        if match:
            section = match[1]
        spans.append({"id": f"{source['id']}:p{index:04d}", "text": paragraph,
                      "start": offset, "end": offset + len(paragraph),
                      "locator": f"{section} / 段落 {index}"})
        offset += len(paragraph) + 1
    return {"title": title, "published_at": meta.get("pubdate") or None,
            "publisher": meta.get("contentsource") or None, "text": text,
            "spans": spans, "attachments": attachments}


def save_snapshot(store, source, raw, parsed, acquisition="network", storage_root=None, max_total_bytes=MAX_TOTAL_BYTES):
    store = Path(store)
    path = store / "manifest.json"
    manifest = read_json(path) if path.exists() else {"schema_version": 1, "documents": {}}
    version = document_version(parsed, source["url"])
    directory = store / "documents" / source["id"] / version
    record = {**parsed, "source_id": source["id"], "source_url": source["url"],
              "kind": source["kind"], "topic": source["topic"],
              "related_ids": source["related_ids"], "status": source["status"],
              "version": version, "raw_sha256": digest(raw), "acquisition": acquisition,
              "fetched_at": datetime.now(timezone.utc).isoformat()}
    old = manifest["documents"].get(source["id"])
    changed = old is None or old["version"] != version
    # Include both serialized snapshots and temporary metadata, not just downloaded HTML.
    planned = 65536 + len(json.dumps(manifest, ensure_ascii=False).encode('utf-8')) * 4
    if not (directory / 'document.json').exists():
        planned += len(raw) + len(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')) * 2
    ensure_capacity(storage_root or store.resolve().parent.parent, planned, max_total_bytes)
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / "document.json").exists():
        (directory / "raw.html").write_bytes(raw)
        write_json(directory / "document.json", record)
    manifest["documents"][source["id"]] = {
        "version": version, "source_url": source["url"], "last_checked_at": record["fetched_at"],
        "status": source["status"], "acquisition": acquisition, "last_check_status": "success",
    }
    write_json(path, manifest)
    return {"source_id": source["id"], "changed": changed, "version": version,
            "spans": len(parsed["spans"])}


def collect(sources_path, store, tls_curve=None):
    config = load_sources(sources_path)
    fetcher = OfficialFetcher(config["allowed_hosts"], tls_curve=tls_curve)
    results = []
    manifest_path = Path(store) / "manifest.json"
    root = Path(store).resolve().parent.parent
    ensure_capacity(root, 65536)
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        configured = {source["id"]: source for source in config["sources"]}
        for source_id, entry in manifest["documents"].items():
            entry["status"] = configured[source_id]["status"] if source_id in configured else "archived"
        write_json(manifest_path, manifest)
    for source in config["sources"]:
        if source["status"] == "archived":
            continue
        if source.get('kind') == 'company_directory':
            results.append({'source_id': source['id'], 'ok': True,
                            'skipped': 'refresh with scripts/update_companies.py'})
            continue
        try:
            raw, content_type, charset = fetcher.fetch(source["url"])
            if content_type not in ("text/html", "application/xhtml+xml"):
                raise ValueError("本版采集只接受 HTML；附件单独记录，不自动下载")
            parsed = parse_html(raw, source, charset)
            results.append({"ok": True, **save_snapshot(store, source, raw, parsed)})
        except StorageLimit as exc:
            results.append({'source_id': source['id'], 'ok': False, 'stopped': 'storage_limit', 'error': str(exc)})
            break
        except (OSError, URLError, ValueError) as exc:
            results.append({"source_id": source["id"], "ok": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            if manifest_path.exists():
                manifest = read_json(manifest_path)
                entry = manifest["documents"].get(source["id"])
                if entry:
                    entry["last_check_status"] = "failed"
                    write_json(manifest_path, manifest)
    report = {"results": results}
    try:
        ensure_capacity(root, len(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')) + 1024)
        write_json(Path(store) / "collection-report.json", report)
    except StorageLimit:
        pass  # Return the stop result without writing beyond the limit.
    return results


def current_documents(store):
    store = Path(store)
    manifest = read_json(store / "manifest.json")
    documents = []
    for source_id, entry in manifest["documents"].items():
        if entry["status"] == "archived":
            continue
        doc = read_json(store / "documents" / source_id / entry["version"] / "document.json")
        raw_path = store / "documents" / source_id / entry["version"] / "raw.html"
        if digest(raw_path.read_bytes()) != doc["raw_sha256"]:
            raise ValueError("原始 HTML 指纹不匹配")
        if (document_version(doc, doc["source_url"]) != doc["version"] or
            doc["version"] != entry["version"] or doc["source_url"] != entry["source_url"]):
            raise ValueError("文档快照内容指纹不匹配")
        for span in doc["spans"]:
            if doc["text"][span["start"]:span["end"]] != span["text"]:
                raise ValueError("证据定位与正文不匹配")
        doc["last_check_status"] = entry.get("last_check_status", "success")
        doc["last_checked_at"] = entry["last_checked_at"]
        documents.append(doc)
    return documents


def search_documents(store, query, top_k=5, successful_only=False, source_ids=None):
    from app.knowledge.hybrid import rerank, ALIASES
    from app.knowledge.database import database_spans
    terms = tokens(query + ' ' + ' '.join(value for key,value in ALIASES.items() if key in query))
    hits = []
    documents = current_documents(store)
    indexed = database_spans(store, documents)
    for doc in documents:
        if source_ids is not None and doc['source_id'] not in source_ids:
            continue
        if successful_only and doc['last_check_status'] != 'success':
            continue
        for span in (indexed.get(doc['source_id'], []) if indexed is not None else doc['spans']):
            overlap = len(terms & tokens(span['text']))
            if overlap:
                title_score = 2 * len(terms & tokens(doc['title']))
                is_heading = re.match(r'^[一二三四五六七八九十]+[、．.]', span['text'])
                heading_bonus = 4 * overlap if is_heading and len(span['text']) <= 40 else 0
                score = overlap + title_score + heading_bonus
                hits.append({"source_id": doc["source_id"], "title": doc["title"],
                             "attachments": doc.get('attachments', []),
                             "source_url": doc["source_url"], "version": doc["version"],
                             "kind": doc["kind"], "status": "待核实，仅供研究",
                             "published_at": doc["published_at"],
                             "last_check_status": doc["last_check_status"],
                             "last_checked_at": doc["last_checked_at"],
                             "score": score, **span})
    ranked = sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))
    return rerank(ranked, query)[:top_k]
