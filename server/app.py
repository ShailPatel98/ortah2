# server/app.py
import os, re, time
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---- Env ----
OPENAI_MODEL_CHAT = os.getenv("OPENAI_MODEL_CHAT", "gpt-4o-mini")
OPENAI_MODEL_EMBED = os.getenv("OPENAI_MODEL_EMBED", "text-embedding-3-small")
BASE_URL = os.getenv("BASE_URL", "https://ortahaus.com")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",")]
PORT = int(os.getenv("PORT", "8000"))
PINECONE_INDEX = os.getenv("PINECONE_INDEX", "ortahaus")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE", "prod")

app = FastAPI(title="Ortahaus Product Guide")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve /ui and /static from /web
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

# ---- lazy clients (no network at import) ----
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

# ---- tiny session store ----
SESSIONS: Dict[str, Dict[str, Any]] = {}
def get_session(session_id: Optional[str]) -> Dict[str, Any]:
    sid = session_id or "default"
    if sid not in SESSIONS:
        SESSIONS[sid] = {
            "created_at": time.time(),
            "hair_type": None,
            "concern": None,
            "history": [],
        }
    return SESSIONS[sid]

# ---- extraction helpers ----
HAIR_TYPES = ["straight","wavy","curly","coily","fine","thick","thin"]
CONCERN_SYNONYMS = {
    "frizz":"frizz", "frizzy":"frizz",
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

def embed(query: str) -> List[float]:
    return get_openai().embeddings.create(model=OPENAI_MODEL_EMBED, input=query).data[0].embedding

def pinecone_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    vec = embed(query)
    res = get_index().query(vector=vec, top_k=top_k, namespace=PINECONE_NAMESPACE, include_metadata=True)
    matches = getattr(res, "matches", None) or []
    out = []
    for m in matches:
        md = m.metadata if hasattr(m, "metadata") else m.get("metadata", {})  # SDK compat
        out.append({
            "title": (md or {}).get("title") or "",
            "url": (md or {}).get("url") or "",
            "how_to_use": (md or {}).get("how_to_use") or "",
            "ingredients": (md or {}).get("ingredients") or "",
            "bullets": (md or {}).get("bullets") or [],
            "score": float(getattr(m, "score", 0.0) or m.get("score", 0.0)),
        })
    return out

def product_html(p: Dict[str, Any]) -> str:
    link = f'<a href="{p["url"]}" target="_blank" rel="noopener" class="rec-link"><strong>{p["title"]}</strong></a>'
    how = f" How to use: {p['how_to_use'].strip()}" if p.get("how_to_use") else ""
    why = ""
    if isinstance(p.get("bullets"), list) and p["bullets"]:
        why = f" Why: {p['bullets'][0]}"
    return f"I’d go with {link}.{why}{how}"

# ---- API ----
class ChatIn(BaseModel):
    message: str
    session_id: Optional[str] = None

@app.post("/chat")
def chat(body: ChatIn, request: Request):
    session = get_session(body.session_id)
    user_msg = (body.message or "").strip()
    session["history"].append({"role": "user", "content": user_msg})

    # slot fill
    if not session.get("hair_type"):
        ht = pick_hair_type(user_msg)
        if ht: session["hair_type"] = ht
    if not session.get("concern"):
        c = pick_concern(user_msg)
        if c: session["concern"] = c

    if not session.get("hair_type"):
        return {"reply": "Got it. What’s your hair type—straight, wavy, curly, or coily?"}
    if not session.get("concern"):
        return {"reply": f"Thanks! What’s your main goal for {session['hair_type']} hair—volume, hold, frizz control, shine, or hydration?"}

    # we have enough -> search
    q = f"{session['hair_type']} hair; concern: {session['concern']}. Best Ortahaus product."
    results = []
    try:
        results = pinecone_search(q, top_k=4)
    except Exception:
        pass

    if results:
        reply_html = product_html(results[0])
    else:
        # fallback if index empty / Pinecone blocked
        if session["concern"] in ("volume","texture"):
            reply_html = product_html({"title":"Corriedale Powder","url":f"{BASE_URL}/products/corriedale-powder","how_to_use":"Tap a little at roots; tousle for lift.","bullets":["Instant matte volume without stiffness."]})
        elif session["concern"] in ("frizz","definition","hydration") and session["hair_type"] in ("wavy","curly","coily"):
            reply_html = product_html({"title":"Merino Cream","url":f"{BASE_URL}/products/merino-cream","how_to_use":"Apply to damp hair; scrunch and air-dry or diffuse.","bullets":["Controls frizz and defines curls with a soft finish."]})
        else:
            reply_html = product_html({"title":"Herdsman Cement","url":f"{BASE_URL}/products/herdsman-cement","how_to_use":"Emulsify a small dab in palms; apply to dry hair; comb or finger-style.","bullets":["Strong, all-day hold without crunch."]})

    session["history"].append({"role": "assistant", "content": reply_html})
    return {"reply": reply_html}
