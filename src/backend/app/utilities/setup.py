from dotenv import load_dotenv

from chromadb import Client, HttpClient
from chromadb.config import Settings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI
# from langchain.chains import RetrievalQA
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.llms import Ollama
from sentence_transformers import CrossEncoder

from .logging_module import logger
from .reranker import CrossEncoderRerankRetriever
from .contextual_compressor import ContextualCompressor
# from file_handler import init_load_docs

# from sentence-transformers import SentenceTransformer

load_dotenv()
# file_logger = logging_module.logger
# docs = init_load_docs()

def generate_response(documents):
    COLLECTION_NAME = "docs_ingestion"
    EMBED_MODEL="sentence-transformers/all-MiniLM-L6-v2"
    RERANKER_MODEL="BAAI/bge-reranker-base"
# docs = []
# doc_folder = "src/documents"
# # Step 1: Load the appropriate doc
# for document in os.listdir(doc_folder):
#     if document.endswith(".pdf"):
#         loader = PyPDFLoader(os.path.join(doc_folder, document))
#         docs += loader.load()
#     elif document.endswith(".txt"):
#         loader = TextLoader(os.path.join(doc_folder, document))
#         docs += loader.load()
#     elif document.endswith(".docx"):  
#         loader = Docx2txtLoader(os.path.join(doc_folder, document))
#         docs += loader.load()
#     elif document.endswith(".csv"):
#         loader = CSVLoader(os.path.join(doc_folder, document))
#         docs += loader.load
#     elif document.endswith(".xls") or document.endswith(".xlsx"):
#         excel_content = pd.read_excel(os.path.join(doc_folder, document))
#         docs += excel_content.to_string()

    logger.info("SPLITTING characters now")

    # Step 2: Chunk the document
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", " "]
    )
    chunks = splitter.split_documents(documents)

    logger.info("ADDING chunk metadata")
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_id"] = i
        chunk.metadata["file_name"] = chunk.metadata.get("source", "").split("/")[-1]

    #Step 3: Bi-encoder embedding model
    embedding_model = HuggingFaceEmbeddings(model_name=EMBED_MODEL)
    reranker_model = CrossEncoder(model_name_or_path=RERANKER_MODEL)

    # Step 4: Store in Chroma DB
    chroma_client = HttpClient(host='localhost', port=8000)
    vector_db = Chroma.from_documents(
        # chunks, embedding_model, persist_directory="./chroma_db"
        documents=chunks, embedding=embedding_model, client=chroma_client, collection_name=COLLECTION_NAME
    )
    # docs_collection = chroma_client.get_collection(COLLECTION_NAME)
    docs_collection = vector_db._collection
    # vectordb.persist()
    logger.info("FINISHED assigning docs_collection")

    # Step 5: Crosss-encoder re-ranker layer
    # retriever = vectordb.as_retriever(search_engine={"k": 10})

    # retriever = CrossEncoderRerankRetriever(
    #     vector_db=vector_db,
    #     # reranker=CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2"),
    #     reranker=CrossEncoder("BAAI/bge-reranker-base")
    # )
    
    # -------------------------
# Factory helper to construct everything
# -------------------------
    def build_rerank_retriever(k=50, top_n=8, use_llm=True, compress_top_n=5):
        compressor = ContextualCompressor(
            chroma_collection=docs_collection,
            embedder_model=embedding_model,
            reranker_model=reranker_model,
            use_llm=use_llm,
            compress_top_n=compress_top_n,
            cache_metadata_key="compressed_v1"
        )
        retriever = CrossEncoderRerankRetriever(
            vectorstore=vector_db,
            embedder=embedding_model,
            reranker=reranker_model,
            compressor=compressor,
            k=k,
            top_n=top_n
        )
        return retriever

    logger.info("CALLING build_erank_retriever")
    retriever = build_rerank_retriever(k=50, top_n=8, use_llm=True, compress_top_n=4)

    logger.info("DECLARING llm instance")
    # Step 6: Declaration and querying
    llm = Ollama(model="mistral", base_url="http://localhost:11434/")
    
    
    prompt = ChatPromptTemplate.from_template("""
    You are a RAG agent.

    Here is the context:
    {context}

    Question: {question}

    Answer concisely and cite sources when relevant.
    """)

    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    
    result = rag_chain.invoke("Summarize the content")
    

    # qa = RetrievalQA.from_chain_type(
    #     llm=llm, retriever=retriever, return_source_documents=True
    # )
    # query = "Summarize the content"
    # result = qa(query)
    
    print(f"Answer: {result}")
    # print(f"Sources: {result['source_documents']}")
    # docs_found = retriever.get_relevant_documents(query) ## uses similarity search
    # all_docs = vectordb.get(include=["documents"])
    # for i, doc in enumerate(all_docs):
    #     # print(f"Chunk {i+1}:\n{doc.page_content}\n{'-' * 50}")
    #     print(f"Chunk {i+1}:\n{doc}\n{'-' * 50}")
    return result