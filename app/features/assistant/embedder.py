"""Text -> vector. The default embedder is free and fully offline; an API embedder can be
switched in with ASSISTANT_EMBEDDER=openai without touching the rest of the assistant."""
import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

import httpx

from app.core.config import settings

_WORD = re.compile(r"[a-z0-9\u0980-\u09ff]+")

# Banglish / Bangla / shorthand -> canonical English, applied before hashing so
# "supplier gula dekhao" and "show suppliers" land close together.
SYNONYMS = {
    "dao": "show", "daw": "show", "dekhao": "show", "dakhao": "show", "dakao": "show", "dekhan": "show",
    "dakhaw": "show", "dekhaw": "show", "chai": "show", "lagbe": "show", "দাও": "show", "দেখাও": "show",
    "list": "list", "talika": "list", "তালিকা": "list", "লিস্ট": "list", "gula": "list", "guli": "list",
    "koto": "count", "koyta": "count", "kotogula": "count", "sonkha": "count", "sokha": "count", "কত": "count", "কয়টা": "count",
    "mot": "total", "sob": "all", "shob": "all", "sobgula": "all", "সব": "all", "মোট": "total",
    "byr": "buyer", "buyers": "buyer", "বায়ার": "buyer", "kreta": "buyer",
    "suppliers": "supplier", "mill": "supplier", "mills": "supplier", "vendor": "supplier", "vendors": "supplier",
    "সাপ্লায়ার": "supplier", "suplayer": "supplier", "supplayer": "supplier", "suplier": "supplier",
    "plms": "plm", "order": "plm", "orders": "plm", "পিএলএম": "plm",
    "rolls": "roll", "রোল": "roll", "than": "roll",
    "fail": "failed", "fails": "failed", "reject": "rejected", "ফেল": "failed",
    "pass": "passed", "পাস": "passed",
    "defects": "defect", "dosh": "defect", "ত্রুটি": "defect", "khut": "defect",
    "schedules": "schedule", "সিডিউল": "schedule", "shedule": "schedule",
    "mash": "month", "mas": "month", "masher": "month", "মাস": "month", "mashe": "month",
    "aj": "today", "aaj": "today", "আজ": "today", "ajke": "today",
    "details": "detail", "bistarito": "detail", "বিস্তারিত": "detail", "bistarit": "detail",
    "report": "report", "reports": "report", "রিপোর্ট": "report",
    "print": "pdf", "prnt": "pdf", "pint": "pdf", "প্রিন্ট": "pdf",
    "consignments": "consignment", "chalan": "consignment", "receive": "consignment", "received": "consignment",
    "materials": "material", "fabric": "material", "fabrics": "material",
    "kpi": "summary", "overview": "summary", "obostha": "summary", "অবস্থা": "summary",
    "score": "scorecard", "grade": "scorecard", "rating": "scorecard", "best": "scorecard", "worst": "scorecard",
    "hold": "hold", "claim": "claim",
    "ki": "what", "kivabe": "how", "keno": "why", "কি": "what", "কী": "what",
}


def normalize_tokens(text: str) -> list[str]:
    words = _WORD.findall(text.lower())
    return [SYNONYMS.get(w, w) for w in words]


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Signed feature hashing over normalised words, word bigrams and character trigrams.

    No model download, deterministic, works offline. Character trigrams make it robust to
    the spelling drift of Banglish ("suplayer", "supplier") and partial names ("bousu").
    """

    dim = 384

    def __init__(self):
        self.name = f"hash-v1-{self.dim}"

    def _features(self, text: str) -> dict[str, float]:
        tokens = normalize_tokens(text)
        feats: dict[str, float] = {}
        for t in tokens:
            feats[f"w:{t}"] = feats.get(f"w:{t}", 0) + 1.0
            padded = f"#{t}#"
            for i in range(len(padded) - 2):
                g = f"c:{padded[i:i + 3]}"
                feats[g] = feats.get(g, 0) + 0.35
        for a, b in zip(tokens, tokens[1:]):
            feats[f"b:{a}_{b}"] = feats.get(f"b:{a}_{b}", 0) + 0.6
        return feats

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, weight in self._features(text).items():
            h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign * (1.0 + math.log(weight))
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAICompatibleEmbedder:
    def __init__(self):
        self.model = settings.ASSISTANT_EMBED_MODEL
        self.name = f"api-{self.model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=settings.ASSISTANT_LLM_TIMEOUT_SECONDS) as client:
            for i in range(0, len(texts), 96):
                res = client.post(
                    f"{settings.ASSISTANT_LLM_BASE_URL.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {settings.ASSISTANT_LLM_API_KEY}"},
                    json={"model": self.model, "input": texts[i:i + 96]},
                )
                res.raise_for_status()
                out.extend(item["embedding"] for item in res.json()["data"])
        return out


@lru_cache
def get_embedder() -> Embedder:
    if settings.ASSISTANT_EMBEDDER == "openai" and settings.ASSISTANT_LLM_BASE_URL:
        return OpenAICompatibleEmbedder()
    return HashingEmbedder()
