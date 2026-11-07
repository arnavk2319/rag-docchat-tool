import math
import time
import contextlib
import numpy as np
from typing import List, Dict, Any

from sklearn.feature_extraction.text import TfidfVectorizer
from langchain_community.llms import Ollama

from .logging_module import logger

class ContextualCompressor:
    def __init__(
        self,
        chroma_collection,
        embedder_model,
        reranker_model,
        use_llm: bool = True,
        compress_top_n: int = 5,
        cache_metadata_key: str = "compressed_v1"
    ):
        self.collection = chroma_collection
        self.embedder = embedder_model
        self.reranker = reranker_model
        self.use_llm = use_llm
        self.compress_top_n = compress_top_n
        self.cache_key = cache_metadata_key
        
    ## Return cached compression by checking the cache key in the document metadata
    def _get_cached_compression(self, doc_meta=Dict[str, any]) -> str:
        return doc_meta.get(self.cache_key) if doc_meta else None
    
    def _set_cached_compression(self, doc_id: str, compressed_text: str):
        try:
            item = self.collection.get(ids=[doc_id])

            meta = item["metedatas"][0].copy() if item and item["metadatas"] else {}
            meta[self.cache_key] = {
                "text": compressed_text,
                "ts": time.time()
            }
            # fetch document and embedding to upsert in the document collection
            doc_text = item['documents'][0] if item["documents"] else ""
            # emb = self.embedder.encode(doc_text, convert_to_numpy=True).tolist()
            emb = self.embedder.embed_documents(doc_text)
            self.collection.upsert(ids=[doc_id], documents=[doc_text], metadatas=[meta], embedding=[emb])
            
        except Exception as e:
            logger.info("Exception while setting cached compression", e)
        
    def compress_passages(self, query: str, passages: List[Dict[str, Any]], use_extractive_fallback: bool = True):
        scores = [p.metadata.get("cross_score") for p in passages]
        normalized_scores = normalize_scores(scores)
        compressed_results = []
        
        logger.info("compressing top-k results")
        
        ## only compress top-K results with llm to save cost
        for i, p in enumerate(passages):
            # doc_id = p.metadata.get("id") or p.metadata.get("doc_id")
            # ## check cache in meta data
            # cache_entry = None
            # if p.get("meta"):
            #     cache_entry = p['meta'].get(self.cache_key)
            # if cache_entry:
            #     compressed_text = cache_entry.get("text") if isinstance(cache_entry, dict) else cache_entry
            #     compressed_results.append({**p, "compressed": compressed_text, "confidence": normalized_scores[i]})
                
            doc_id = p.metadata.get("id") if "id" in p.metadata else p.metadata.get("doc_id") if "doc_id" in p.metadata else None

            logger.info("check cache in metadata")
            # Check cache in metadata
            cache_entry = None
            if self.cache_key in p.metadata:
                cache_entry = p.metadata[self.cache_key]
                
            logger.info("checking cache entry")

            if cache_entry:
                # If cache_entry is a dict, get "text" key; else use as is
                if isinstance(cache_entry, dict) and "text" in cache_entry:
                    compressed_text = cache_entry["text"]
                else:
                    compressed_text = cache_entry

                # Build the new dictionary for compressed_results
                # Use metadata + new fields
                # new_entry = dict(p.metadata)  # copy metadata as base
                new_entry = dict(p.metadata) | {
                    "compressed": compressed_text,
                    "confidence": normalized_scores[i]
                }
                compressed_results.append(new_entry)
                
            logger.info("choosing compression mode")
                
            ## choose compression mode
            if self.use_llm and i < self.compress_top_n:
                try:
                    # text_to_compress = p.metadata["text"] if "text" in p.metadata else p.page_content
                    
                    # ensure text_to_compress is always a string
                    if hasattr(p, "metadata") and isinstance(p.metadata, dict):
                        text_to_compress = p.metadata["text"] if "text" in p.metadata else p.page_content
                    elif hasattr(p, "page_content"):
                        text_to_compress = p.page_content
                    else:
                        text_to_compress = ""  # fallback to empty string if nothing exists

                    # enforce string
                    text_to_compress = str(text_to_compress) if text_to_compress is not None else ""
                    compressed_text = llm_compress(query, text_to_compress)
                    if not compressed_text and use_extractive_fallback:
                        compressed_text = extractive_compress(text_to_compress, n_sentences=3)
                except Exception as e:
                    # fallback
                    print("LLM compression failed:", e)
                    compressed_text = extractive_compress(text_to_compress, n_sentences=3)
            else:
                # fallback/extractive for lower-ranked passages
                compressed_text = extractive_compress(text_to_compress, n_sentences=2)

            # cache compressed_text back to chroma metadata (best-effort)
            if doc_id:
                with contextlib.suppress(Exception):
                    self._set_cached_compression(doc_id, compressed_text)
            compressed_results.append({**p, "compressed": compressed_text, "confidence": normalized_scores[i]})
        return compressed_results


def normalize_scores(scores: List[float]) -> List[float]:
    if not scores:
        return []
    
    ma, mi = max(scores), min(scores)
    rnge = (ma - mi) if ma != mi else 1e-9
    return [(s - mi) / rnge for s in scores]

## Produces an extractive summary of a text by selecting the most "important" sentences using TF-IDF scoring.
def extractive_compress(text: str, n_sentences: int = 3) -> str:
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    if not sentences:
        return str[:512]
    tf_idf_vector = TfidfVectorizer(stop_words='english').fit_transform(sentences)
    scores = np.asarray(tf_idf_vector.sum(axis=1).ravel())
    top_idx = np.argsort(scores)[-n_sentences][::-1]
    selected = [sentences[i] for i in sorted(top_idx)]
    return ".".join(selected)

## LLM-based and query aware compressor
COMPRESS_PROMPT_TEMPLATE = """
You are a compression assistant. Given a user query and one passage, return a concise, factual, query-focused summary.

Rules:
- Keep it short (<= 120 tokens) and preserve only facts relevant to the query.
- Do not hallucinate or add new facts.
- If passage is unrelated to query, return an empty string.
- Output plain text only.

Query:
{query}

Passage:
{passage}

Return:
"""
## Generates a compressed version of a passage using a language model, guided by a query and a prompt template.
def llm_compress(query: str, passage: str, max_tokens: int = 160) -> str:
    logger.info("going into llm_compress")
    query_text = str(query) if query is not None else ""
    passage_text = str(passage) if passage is not None else ""
    
    prompt = COMPRESS_PROMPT_TEMPLATE.format(query = query_text.strip(), passage = passage_text.strip())
    
    llm = Ollama(model="mistral", base_url="http://localhost:11434/")
    
    logger.info("llm generate response")
    response = llm.generate(prompts=[prompt], max_tokens=max_tokens, temperature=0.0)
    # extract the text from the response object
    # depending on Ollama version, you may use response.generations or response.text
    logger.info("checking hasattr")
    if isinstance(response, list) and len(response) > 0:
        output = response[0].text  # first generation
    else:
        raise ValueError("Cannot extract generated text from Ollama response")
    # if hasattr(response, "generations") and len(response.generations) > 0:
    #     output = response.generations[0].text
    # elif hasattr(response, "text"):
    #     output = response.text
    # else:
    #     raise ValueError("Cannot extract generated text from Ollama response")
    return output.strip()
    
