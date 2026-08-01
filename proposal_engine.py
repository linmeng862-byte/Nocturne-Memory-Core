"""
Proposal engine — LLM scans new memories for slang/encyclopedia candidates.
Does NOT auto-write. Proposals wait for Claude to approve/reject.
"""

import json
import os
import time
from pathlib import Path


PROPOSAL_PROMPT = """分析以下内容，判断是否包含新的梗词/暗语或值得收录的概念。

只输出 JSON，无其他内容：
{
  "slang": [
    {"term": "梗词", "meaning": "含义", "type": "slang"}
  ],
  "concepts": [
    {"term": "概念名", "category": "恋爱|情绪|人际|偏好|工作|学习|健康", "summary": "一句话概括", "type": "encyclopedia"}
  ]
}

如果没有发现，返回 {"slang": [], "concepts": []}。
只收录明显的新梗或反复讨论的核心概念。不要强行提取。

内容：
{content}"""


class ProposalEngine:
    """Scans content for slang/encyclopedia candidates. Writes proposals, never auto-commits."""

    def __init__(self, dehydrator, buckets_dir: str):
        self.dehydrator = dehydrator
        self.proposals_file = Path(buckets_dir) / "evolution" / "_proposals.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.proposals_file.exists():
            self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
            self.proposals_file.write_text("[]", encoding="utf-8")

    def _load(self) -> list:
        try:
            return json.loads(self.proposals_file.read_text("utf-8"))
        except Exception:
            return []

    def _save(self, proposals: list):
        self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
        self.proposals_file.write_text(
            json.dumps(proposals, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    async def scan(self, content: str, source_bucket_id: str = "") -> list:
        """Scan content for new slang/concepts. Returns new proposal IDs."""
        if not self.dehydrator.api_available or len(content) < 50:
            return []

        try:
            resp = await self.dehydrator.client.chat.completions.create(
                model=self.dehydrator.model,
                messages=[{"role": "user", "content": PROPOSAL_PROMPT.format(content=content[:2000])}],
                max_tokens=500,
                temperature=0.2,
            )
            raw = resp.choices[0].message.content.strip()
            # Parse JSON
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw.rsplit("```", 1)[0]
            result = json.loads(raw.strip())
        except Exception:
            return []

        proposals = self._load()
        new_ids = []

        for item in result.get("slang", []) + result.get("concepts", []):
            term = item.get("term", "").strip()
            if not term:
                continue
            # Check for duplicates
            existing = [p for p in proposals if p.get("term") == term and p.get("status") == "pending"]
            if existing:
                continue

            pid = f"prop-{int(time.time())}-{abs(hash(term)) % 10000:04d}"
            proposal = {
                "id": pid,
                "term": term,
                "meaning": item.get("meaning", item.get("summary", "")),
                "type": item.get("type", "slang"),
                "category": item.get("category", ""),
                "source_bucket_id": source_bucket_id,
                "status": "pending",
                "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            proposals.append(proposal)
            new_ids.append(pid)

        if new_ids:
            self._save(proposals)

        return new_ids

    def list_pending(self) -> list:
        """Return all pending proposals."""
        return [p for p in self._load() if p.get("status") == "pending"]

    def accept(self, proposal_id: str, evolution_engine) -> bool:
        """Accept a proposal — writes to slang or encyclopedia via evolution_engine."""
        proposals = self._load()
        for p in proposals:
            if p.get("id") == proposal_id and p.get("status") == "pending":
                if p.get("type") == "slang":
                    # Write to slang
                    import frontmatter
                    from utils import generate_bucket_id, sanitize_name, safe_path, now_iso
                    metadata = {
                        "type": "slang",
                        "term": p["term"],
                        "meaning": p.get("meaning", ""),
                        "first_occurrence": p.get("created", ""),
                        "origin_bucket_id": p.get("source_bucket_id", ""),
                        "usage_count": 1,
                        "emotional_load": 0.5,
                        "is_inside_joke": True,
                        "example": "",
                        "related_bucket_ids": [p.get("source_bucket_id", "")] if p.get("source_bucket_id") else [],
                        "last_seen": p.get("created", ""),
                        "created": p.get("created", ""),
                    }
                    evolution_engine._write_artifact("slang", metadata, p.get("meaning", ""))
                elif p.get("type") == "encyclopedia":
                    # Write to encyclopedia
                    import frontmatter
                    from utils import now_iso
                    metadata = {
                        "type": "encyclopedia",
                        "term": p["term"],
                        "category": p.get("category", "观念"),
                        "first_bucket_id": p.get("source_bucket_id", ""),
                        "evolution": [{
                            "date": p.get("created", now_iso()),
                            "note": p.get("meaning", ""),
                            "bucket_id": p.get("source_bucket_id", ""),
                        }],
                        "related_bucket_ids": [p.get("source_bucket_id", "")] if p.get("source_bucket_id") else [],
                        "created": p.get("created", now_iso()),
                        "last_updated": p.get("created", now_iso()),
                    }
                    evolution_engine._write_artifact("encyclopedia", metadata, p.get("meaning", ""))
                p["status"] = "accepted"
                self._save(proposals)
                return True
        return False

    def reject(self, proposal_id: str) -> bool:
        """Reject a proposal."""
        proposals = self._load()
        for p in proposals:
            if p.get("id") == proposal_id and p.get("status") == "pending":
                p["status"] = "rejected"
                self._save(proposals)
                return True
        return False
