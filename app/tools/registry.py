"""Runtime owns authorization; tool handlers own strict argument validation."""
from app.tools.policy_tools import ToolError
from copy import deepcopy

SPECS = {
    'search_documents': {'name':'search_documents', 'description':'检索本地官方政策', 'arguments': {'query':'string, 1..500'}},
    'read_source': {'name':'read_source', 'description':'读取已知政策章节', 'arguments': {'source_id':'known source id','section':'string, 1..100'}},
}

class ToolRegistry:
    def __init__(self, tools, policy):
        self.tools, self.policy = tools, policy

    def manifest(self):
        return [deepcopy(SPECS[name]) for name in self.policy.allowed_tools]

    def execute(self, name, arguments):
        if name not in self.policy.allowed_tools:
            raise ToolError('运行策略不允许调用该工具')
        return self.tools.execute(name, arguments)
