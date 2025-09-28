from dotenv import load_dotenv
import logging_module

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import ChatOpenAI
from langchain.chains import RetrievalQA
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.llms import Ollama

from file_handler import init_load_docs

# from sentence-transformers import SentenceTransformer

load_dotenv()
file_logger = logging_module.logger
docs = init_load_docs()

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


# Step 2: Chunk the document
splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
    length_function=len,
    separators=["\n\n", "\n", " "]
)
chunks = splitter.split_documents(docs)

#Step 3: Declare embeddings
embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")


# Step 4: Store in Chroma DB
vectordb = Chroma.from_documents(
    chunks, embedding_model, persist_directory="./chroma_db"
)
vectordb.persist()

# Step 5: Retriever
retriever = vectordb.as_retriever(search_engine={"k": 10})

# Step 6: Declaration and querying
llm = Ollama(model="llama2")
qa = RetrievalQA.from_chain_type(
    llm=llm, retriever=retriever, return_source_documents=True
)

query = "Summarize only the things for travel as well as the reservation details"
result = qa(query)
# docs_found = retriever.get_relevant_documents(query) ## uses similarity search
# all_docs = vectordb.get(include=["documents"])
# for i, doc in enumerate(all_docs):
#     # print(f"Chunk {i+1}:\n{doc.page_content}\n{'-' * 50}")
#     print(f"Chunk {i+1}:\n{doc}\n{'-' * 50}")

print(f"Answer: {result['result']}")
print(f"Sources: {result['source_documents']}")