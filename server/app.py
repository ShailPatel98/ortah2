# server/app.py
import os, re, time
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------- Env ----------
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
OPENAI_MODEL_EMBED = os.getenv("OPENAI_MODEL_EMBED", "text-embedding-3-small")
BASE_URL = os.getenv("BASE_URL", "https://ortahaus.com")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "ortahaus")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "prod")

# ---------- App & Static ----------
app = FastAPI(title="Ortahaus Product Guide")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
INDEX_HTML = WEB_DIR / "index.html"
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

@app.get("/ui", response_class=HTMLResponse)
def ui():
    if INDEX_HTML.exists():
        return INDEX_HTML.read_text(encoding="utf-8")
    return HTMLResponse("<h1>UI not found</h1><p>Put index.html under /web.</p>", status_code=500)

@app.get("/favicon.ico")
def favicon():
    ico = WEB_DIR / "favicon.ico"
    if ico.exists():
        return FileResponse(ico)
    return JSONResponse({"detail": "no favicon"}, status_code=404)

@app.get("/healthz")
def healthz():
    return {"ok": True}

# ---------- Lazy Clients ----------
_openai = None
_index = None
def get_openai():
    global _openai
    if _openai is None:
        from openai import OpenAI
        _openai = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai

def get_index():
    global _index
    if _index is None:
        from pinecone import Pinecone
        _index = Pinecone(api_key=os.environ["PINECONE_API_KEY"]).Index(PINECONE_INDEX)
    return _index

# ---------- Session & Extractors ----------
SESSIONS: Dict[str, Dict[str, Any]] = {}

def session_for(sid: Optional[str]) -> Dict[str, Any]:
    s = sid or "default"
    if s not in SESSIONS:
        SESSIONS[s] = {"created_at": time.time(), "hair_type": None, "concern": None, "history": []}
    return SESSIONS[s]

HAIR_TYPES = ["straight","wavy","curly","coily","fine","thick","thin"]
CONCERN_SYNONYMS = {
    "frizz":"frizz","frizzy":"frizz",
    "volume":"volume","volumize":"volume","lift":"volume",
    "hold":"hold","strong hold":"hold","control":"hold",
    "shine":"shine","gloss":"shine",
    "texture":"texture","grit":"texture",
    "hydrate":"hydration","hydration":"hydration","moisture":"hydration","dry":"hydration","dryness":"hydration",
    "oily":"oil control","greasy":"oil control","oil":"oil control",
    "definition":"definition",
}
def pick_hair_type(text: str) -> Optional[str]:
    t = text.lower()
    for ht in HAIR_TYPES:
        if re.search(rf"\b{re.escape(ht)}\b", t):
            return "thin" if ht == "fine" else ht
    return None
def pick_concern(text: str) -> Optional[str]:
    t = text.lower()
    for k, v in CONCERN_SYNONYMS.items():
        if re.search(rf"\b{re.escape(k)}\b", t):
            return v
    return None

# ---------- Retrieval ----------
def embed(query: str) -> List[float]:
    return get_openai().embeddings.create(model=OPENAI_MODEL_EMBED, input=query).data[0].embedding

def retrieve_products(hair_type: str, concern: str, top_k: int = 5) -> List[Dict[str, Any]]:
    vec = embed(f"{hair_type} hair; concern: {concern}. Best Ortahaus product.")
    res = get_index().query(vector=vec, top_k=top_k, namespace=PINECONE_NAMESPACE, include_metadata=True)
    out = []
    for m in getattr(res, "matches", []) or []:
        md = m.metadata if hasattr(m, "metadata") else m.get("metadata", {})
        out.append({
            "title": (md or {}).get("title") or "",
            "url": (md or {}).get("url") or "",
            "how_to_use": (md or {}).get("how_to_use") or "",
            "ingredients": (md or {}).get("ingredients") or "",
            "bullets": (md or {}).get("bullets") or [],
        })
    return out[:top_k]

# ---------- LLM Orchestrator ----------
SYSTEM_PROMPT = """You are the Ortahaus Product Guide.
Style: warm, conversational, brief. Sound like a knowledgeable stylist.
Behavior:
- Greet naturally. If info is missing, ask ONE short follow-up at a time.
- Track context: hair type and main concern.
- Once you have both, recommend ONE best product.
- Explain *why* in a single line, then give 1–2 "how to use" bullets if available.
- Output HTML-safe text. For links, use plain URLs or <a href="...">...</a> (we support it).
- Never recommend competitors. Only recommend products from the provided context.
"""

def build_context_block(products: List[Dict[str, Any]]) -> str:
    lines = []
    for p in products:
        bullets = " • ".join(p.get("bullets") or [])[:300]
        how = p.get("how_to_use") or ""
        lines.append(
            f"- {p['title']} | {p['url']} | bullets: {bullets} | how_to_use: {how}"
        )
    return "\n".join(lines)

def md_links_to_html(s: str) -> str:
    # convert [text](url) to <a href="url" target="_blank">text</a>
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        s,
    )

# ---------- API ----------
class ChatIn(BaseModel):
    message: str
    session_id: Optional[str] = None

@app.post("/chat")
def chat(body: ChatIn, request: Request):
    sess = session_for(body.session_id)
    user = (body.message or "").strip()
    sess["history"].append({"role": "user", "content": user})

    # Update slots from the latest message
    ht = sess.get("hair_type") or pick_hair_type(user)
    if ht: sess["hair_type"] = ht
    concern = sess.get("concern") or pick_concern(user)
    if concern: sess["concern"] = concern

    # Build messages for the model
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Short summary of known session state
    known = []
    if sess.get("hair_type"): known.append(f"hair_type={sess['hair_type']}")
    if sess.get("concern"): known.append(f"concern={sess['concern']}")
    if known:
        msgs.append({"role": "system", "content": "Known context: " + ", ".join(known)})

    # If we have enough info, add product context
    products = []
    if sess.get("hair_type") and sess.get("concern"):
        try:
            products = retrieve_products(sess["hair_type"], sess["concern"], top_k=5)
        except Exception:
            products = []
        if products:
            msgs.append({"role": "system", "content": "Product context:\n" + build_context_block(products)})

    # Add last 6 turns of history (lightweight memory)
    for m in (sess["history"] or [])[-6:]:
        msgs.append({"role": m["role"], "content": m["content"]})

    # Ask model to reply; if no products yet, it should ask one clear follow-up
    openai = get_openai()
    resp = openai.chat.completions.create(
        model=OPENAI_MODEL_CHAT,
        messages=msgs,
        temperature=0.7,
        top_p=1.0,
        max_tokens=350,
    )
    text = resp.choices[0].message.content.strip()

    # Linkify any markdown links
    text = md_links_to_html(text)

    sess["history"].append({"role": "assistant", "content": text})
    return {"reply": text}
