import logging
from fastapi import HTTPException, APIRouter, File, UploadFile
from tika import parser
import asyncio

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/ping-checkpoint", tags=["ping-checkpoint"])
def pingCheckpoint():
    logger.info("Checking endpoint status")
    return "Returning OK 200"

async def parse_contents(data: bytes):
    try:
        logger.info("Now parsing file contents")
        return await asyncio.to_thread(parser.from_buffer, data)
    except Exception:
        logger.error(f"Exception while parsing: {Exception}")

@router.post("/upload", tags=["upload"])
async def upload_file(file: UploadFile = File(...)):
    try:
        logger.info("initializing file read")
        data = await file.read()
        
        logger.info("read complete, passing to parser")
        parsed_data = await parse_contents(data)
        
        logger.info("parsing complete, reading parsed data")
        text = parsed_data.get("content", "")
        metadata = parsed_data.get("metadata", {})
        
        logger.info("returning successful file reads")
        return {
            "filename": file.filename,
            "file_length": len(text),
            "metadata": metadata
        }
    except Exception:
        logger.error(f"Exception: {Exception}")
    