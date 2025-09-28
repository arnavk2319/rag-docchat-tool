import os
import pandas as pd
import logging_module
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader, CSVLoader, UnstructuredExcelLoader
from langchain.docstore.document import Document

persist_dir = os.path.abspath("./chroma_db")
print("Exists:", os.path.exists(persist_dir))
print("Writable?", os.access(persist_dir, os.W_OK))

file_logger = logging_module.logger ## debug, info, warning, error, critical

def init_load_docs():
    docs = []
    doc_folder = "src/documents"

    for document in os.listdir(doc_folder):
        if document.endswith(".pdf"):
            docs += load_pdf(doc_folder, document)
        elif document.endswith(".txt"):
            docs += load_txt(doc_folder, document)
        elif document.endswith(".docx"):
            docs += load_doc(doc_folder, document)
        elif document.endswith(".csv"):
            docs += load_csv(doc_folder, document)
        elif document.endswith(".xls") or document.endswith(".xlsx"):
            docs += load_xls(doc_folder, document)
            
    return docs

def load_pdf(doc_folder, doc):
    try:
        file_logger.debug("reading a PDF file")
        return PyPDFLoader(os.path.join(doc_folder, doc)).load()
    except Exception as e:
        print(f"Exception while loading PDF: {e}")
        
def load_txt(doc_folder, doc):
    try:
        file_logger.debug("reading a TXT file")
        return TextLoader(os.path.join(doc_folder, doc)).load()
    except Exception as e:
        print(f"Exception while loading TXT: {e}")
        
def load_doc(doc_folder, doc):
    try:
        file_logger.debug("reading a DOC file")
        return Docx2txtLoader(os.path.join(doc_folder, doc)).load()
    except Exception as e:
        print(f"Exception while loading DOC: {e}")
        
def load_csv(doc_folder, doc):
    try:
        file_logger.debug("reading a CSV file")
        return CSVLoader(os.path.join(doc_folder, doc)).load()
    except Exception as e:
        print(f"Exception while loading CSV: {e}")
        
def load_xls(doc_folder, doc):
    try:
        file_logger.debug("reading an EXCEL file")
        return UnstructuredExcelLoader(os.path.join(doc_folder, doc)).load()
    except Exception as e:
        print(f"Exception while loading EXCEL: {e}")