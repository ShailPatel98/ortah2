#!/usr/bin/env python3
import os, json
from typing import Any, Dict, List

from openai import OpenAI
from pinecone import Pinecone

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","")
OPENAI_MODEL_EMBED = os.getenv("OPENAI_MODEL_EMBED","text-embedding-3-small")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY","")
PINECONE_INDEX = os.getenv("PINECONE_INDEX","ortahaus")
PINECONE_NAMESPACE = os.getenv("PINECONE_NAMESPACE","prod")

DATA_FILE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "products.json"))

def sanitize(md: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k,v in (md or {}).items():
        if v is None: continue
        if isinstance(v,(str,int,float,bool)):
            out[k]=v
        elif isinstance(v,list):
            out[k]=[str(x) for x in v if x is not None][:32]
        else:
            out[k]=str(v)[:1000]
    return out

def main():
    if not os.path.exists(DATA_FILE):
        raise SystemExit(f"{DATA_FILE} not found. Run the scraper first.")

    items = json.load(open(DATA_FILE,"r",encoding="utf-8"))
    if not items:
        raise SystemExit("No products found in products.json")

    oai = OpenAI(api_key=OPENAI_API_KEY or None)
    pc = Pinecone(api_key=PINECONE_API_KEY or None)
    index = pc.Index(PINECONE_INDEX)

    def embed(text: str) -> List[float]:
        text = (text or "").replace("\n"," ")
        return oai.embeddings.create(model=OPENAI_MODEL_EMBED, input=text).data[0].embedding

    batch = []
    for r in items:
        body = " ".join(filter(None, [
            r.get("title",""), r.get("description",""),
            " ".join(r.get("bullets",[])[:8]),
            f"How to use: {r.get('how_to_use','')}",
            f"Ingredients: {r.get('ingredients','')}",
        ]))[:7000]
        vec = embed(body)
        meta = sanitize({
            "title": r.get("title",""),
            "url": r.get("url",""),
            "how_to_use": r.get("how_to_use",""),
            "ingredients": r.get("ingredients",""),
            "bullets": r.get("bullets",[]),
        })
        batch.append({"id": r.get("id") or r.get("url"), "values": vec, "metadata": meta})

    # upsert in chunks
    B = 50
    for i in range(0,len(batch),B):
        chunk = batch[i:i+B]
        index.upsert(vectors=chunk, namespace=PINECONE_NAMESPACE)
        print(f"Upserted {len(chunk)} vectors…")

    print("Done.")

if __name__ == "__main__":
    main()
