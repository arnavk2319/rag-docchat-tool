import os
import pandas as pd
from enum import Enum
from .logging_module import logger
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, TextLoader, CSVLoader, UnstructuredExcelLoader

class MIME_TYPE(Enum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    DOC = "application/msword"
    XLS = "application/vnd.ms-excel"
    CSV = "text/csv"
    JPG = "image/jpeg"
    TXT = "text/plain"

persist_dir = os.path.abspath("./chroma_db")
print("Exists:", os.path.exists(persist_dir))
print("Writable?", os.access(persist_dir, os.W_OK))

def parse_uploaded_file(file_path, mime_type):
    logger.info(f"PARSING UPLOADED FILE {file_path}: {mime_type}")
    parsed_doc = None
    if mime_type == MIME_TYPE.PDF.value:
        parsed_doc = load_pdf(file_path)
    elif mime_type == MIME_TYPE.TXT.value:
        parsed_doc = load_txt(file_path)
    elif mime_type in [MIME_TYPE.DOC.value, MIME_TYPE.DOCX.value]:
        logger.info("File is DOC or DOCX")
        parsed_doc = load_doc(file_path)
    elif mime_type == MIME_TYPE.CSV.value:
        parsed_doc = load_csv(file_path)
    elif mime_type == MIME_TYPE.XLS.value:
        parsed_doc = load_xls(file_path)
    logger.info(f"READING PARSED DOC {parsed_doc}")
    return parsed_doc
    
# def init_load_docs():
#     docs = []
#     file_path = "src/documents"
#     for document in os.listdir(file_path):
#         if document.endswith(".pdf"):
#             docs += load_pdf(file_path, document)
#         elif document.endswith(".txt"):
#             docs += load_txt(file_path, document)
#         elif document.endswith(".docx"):
#             docs += load_doc(file_path, document)
#         elif document.endswith(".csv"):
#             docs += load_csv(file_path, document)
#         elif document.endswith(".xls") or document.endswith(".xlsx"):
#             docs += load_xls(file_path, document)
#     return docs

def load_pdf(file_path):
    try:
        logger.debug("reading a PDF file")
        if len(file_path) > 0:
            return PyPDFLoader(file_path).load()
    except Exception as e:
        print(f"Exception while loading PDF: {e}")
        
def load_txt(file_path):
    try:
        logger.debug("reading a TXT file")
        if len(file_path) > 0:
            return TextLoader(file_path).load()
    except Exception as e:
        print(f"Exception while loading TXT: {e}")
        
def load_doc(file_path):
    try:
        logger.debug("reading a DOC file")
        if len(file_path) > 0:
            return Docx2txtLoader(file_path).load()
    except Exception as e:
        print(f"Exception while loading DOC: {e}")
        
def load_csv(file_path):
    try:
        logger.debug("reading a CSV file")
        if len(file_path) > 0:
            return CSVLoader(file_path, encoding="utf-8").load()
    except Exception as e:
        print(f"Exception while loading CSV: {e}")
        
def load_xls(file_path):
    try:
        logger.debug("reading an EXCEL file")
        if len(file_path) > 0:
            return UnstructuredExcelLoader(file_path).load()
    except Exception as e:
        print(f"Exception while loading EXCEL: {e}")