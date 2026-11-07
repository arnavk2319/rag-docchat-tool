
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores import VectorStore
from pydantic import BaseModel, ConfigDict
from typing import List, Any

from .contextual_compressor import ContextualCompressor
from .logging_module import logger

class CrossEncoderRerankRetriever(BaseRetriever):
    vectorstore: VectorStore
    embedder: Any
    reranker: Any
    compressor: Any | None = None
    k: int = 50
    top_n: int = 8
    
    model_config = ConfigDict(arbitrary_types_allowed=True)
    # def __init__(self, vectorstore, embedder, reranker, compressor: ContextualCompressor, k=50, top_n=8):
    #     self.vectorstore = vectorstore
    #     self.embedder = embedder
    #     self.reranker = reranker
    #     self.compressor = compressor
    #     self.k = k
    #     self.top_n = top_n

    def _get_relevant_documents(self, query: str) -> List[Document]:
        # # 1. vector search using bi-encoder
        # docs = self.vdb.similarity_search(query, k=self.k)

        # # 2. re-rank using cross-encoder
        # pairs = [(query, d.page_content) for d in docs]
        # scores = self.reranker.predict(pairs)

        # # 3. pick top reranked docs
        # reranked = [
        #     doc for _, doc in sorted(zip(scores, docs), reverse=True)
        # ][:self.top_n]

        # return reranked
    
        logger.info("enbedding the query")
        # 1) Bi-encoder retrieval via chroma similarity search
        # q_emb = self.embedder.encode(query, convert_to_numpy=True).tolist()
        q_emb = self.embedder.embed_query(query)
        # results = self.vectorstore.query(query_embeddings=[q_emb], n_results=self.k, include=['documents','metadatas','distances','ids'])
        docs = self.vectorstore.similarity_search_by_vector(embedding=[q_emb], k=self.k)
        # docs_raw = []
        # for doc_text, meta, dist, doc_id in zip(results['documents'][0], results['metadatas'][0], results['distances'][0], results['ids'][0]):
        #     docs_raw.append({
        #         "id": doc_id,
        #         "text": doc_text,
        #         "meta": meta or {},
        #         "dist": dist
        #     })
        # docs_raw.extend(
        #     {"id": doc_id, "text": doc_text, "meta": meta or {}, "dist": dist}
        #     for doc_text, meta, dist, doc_id in zip(
        #         results['documents'][0],
        #         results['metadatas'][0],
        #         results['distances'][0],
        #         results['ids'][0],
        #     )
        # )

        # if not docs_raw:
        #     return []

        logger.info("making the pairs")
        # 2) Cross-encoder reranking (batch)
        pairs = [(query, d.page_content) for d in docs]
        scores = self.reranker.predict(pairs, show_progress_bar=False, batch_size=16)
        
        logger.info("assigning cross_score")
        for d, s in zip(docs, scores):
            d.metadata['cross_score'] = float(s)

        # sort by cross_score desc and pick top_k
        # docs_sorted = sorted(docs, key=lambda x: x['cross_score'], reverse=True)
        docs_sorted = sorted(docs, key=lambda x: x.metadata['cross_score'], reverse=True)
        top_candidates = docs_sorted[:self.top_n]

        logger.info("compressed the packages")
        # 3) Contextual compression (query-aware) on top candidates
        compressed = self.compressor.compress_passages(query, top_candidates)

        logger.info("returning langchain documents")
        # 4) Return as LangChain Document objects with compressed text in page_content
        out_docs = []
        for item in compressed:
            page_content = item.metadata.get("compressed") or item.metadata.get("text")[:400]
            # meta = item.get("meta", {}).copy()
            meta = item.metadata.copy()
            meta.update({
                "source_id": item.metadata.get("id"),
                "orig_cross_score": item.metadata.get("cross_score"),
                "compress_confidence": item.metadata.get("confidence")
            })
            out_docs.append(Document(page_content=page_content, metadata=meta))

        logger.info("returning the docs")
        return out_docs