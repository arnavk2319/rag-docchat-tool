import asyncio
# import magic
import filetype
from aiohttp import web
import aiofiles
import tempfile
import shutil
import os
from pathlib import Path
from fastapi import HTTPException, APIRouter, File, UploadFile, Request
from fastapi.responses import Response
# from tika import parser
from ..utilities.logging_module import logger
from ..utilities.file_handler import parse_uploaded_file
from ..utilities.setup import generate_response

router = APIRouter()

MAX_FILE_SIZE = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = [".pdf", ".docx", ".txt", ".csv", ".xls", ".xlsx"]
ALLOWED_MIME_TYPES = [
    "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "text/csv","image/jpeg", "application/msword", "text/plain"
]

# def validate_file_type(file: UploadFile):
#     try:
#         # content_type = file.content_type
#         # content = await file.read(2048) # read the first 2KB to detect type
#         # file.file.seek(0)  #reset the file counter
#         # mime_type = magic.from_buffer(content, mime=True)

#         # logger.info("Checking file extension")
#         # if mime_type not in allowed_types:
#         #     return HTTPException(status_code=500, detail="File format is not allowed")

#         # return {
#         #     "valid_format": True,
#         #     "mime_type": mime_type
#         # }
#         # return await asyncio.to_thread(parser.from_buffer, data)
#     except Exception as e:
#         logger.error(f"Exception while parsing: {e}")

@router.get("/ping-checkpoint", tags=["ping-checkpoint"])
def pingCheckpoint():
    logger.info("Checking endpoint status")
    return "Returning OK 200"

@router.post("/validate-upload", tags=["upload"])
async def upload_file(request: Request, file: UploadFile = File(...)):
    try:
        form = await request.form()
        uploaded_file = form.get('file')
        if uploaded_file is None:
            raise FileNotFoundError("File not uploaded")
        
        contents = await file.read()
        # Check file size
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            # raise HTTPException(status_code=500, detail="File is too big!")
            raise ValueError(f"File size is too large {content_length}")
        
        ## validate file type
        logger.info("validating file mime type")
        
        # response = await validate_file_type(file)
        # mime_type = magic.from_buffer(contents[:2048], mime=True)  ## Read first 2KB
        file_kind = filetype.guess(contents)
        mime_type = file_kind.mime if file_kind else None
        file_ext = Path(file.filename).suffix.lower()
        if file_ext and file_ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"File extension not allowed {file_ext}")
        if mime_type and mime_type not in ALLOWED_MIME_TYPES:
            raise ValueError(f"File MIME type not allowed {mime_type}")
            
        ## Save to a temp file location
        logger.info("Saving to a temporary file")
        if not file.filename:
            raise ValueError("Filename is required")
        suffix = Path(file.filename).suffix.lower()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        
        logger.info("initializing file read")
        parsed_document = parse_uploaded_file(tmp_path, mime_type)
        # if parsed_document in None:
        #     raise ValueError("File not parsed successfully")
        # else:
        logger.info(f"FILE CONTENTS PARSED {parsed_document}")
        generated_summary = generate_response(parsed_document)
        
        logger.info("returning successful file reads")
        return {
            "filename": file.filename,
            "content": parsed_document,
            "summary": generated_summary
        }
        
    except FileNotFoundError as e:
        logger.error(f"File not found exception: {e}")
        raise HTTPException(status_code=400, detail=f"file not found exception occurred: {e}")
    except ValueError as e:
        logger.error(f"Exception on validating file {e}")
        raise HTTPException(status_code=400, detail=f"validation exception occurred: {e}")
    except Exception as e:
        logger.error(f"Exception on UPLOAD: {e}")
        raise HTTPException(status_code=500, detail=f"An error occurred: {e}")
    finally:
        os.remove(tmp_path)
    