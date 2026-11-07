import logging

logger = logging.getLogger("uvicorn")
logger.setLevel(logging.INFO)

handler = logging.StreamHandler()
# formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

##set formatter
# handler.setFormatter(formatter)
##set handler
logger.addHandler(handler)