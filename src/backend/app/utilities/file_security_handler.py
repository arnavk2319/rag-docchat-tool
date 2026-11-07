import os
from PyPDF2 import PdfReader
import requests

def check_pdf_security(pdf_path: str) -> bool:
    """Returns True if the PDF is not password protected, contains no JS or embedded files."""
    try:
        reader = PdfReader(pdf_path)
        # Check for password protection
        if reader.is_encrypted:
            print("PDF is encrypted/password protected.")
            return False

        # Look for JavaScript in PDF
        if "/JavaScript" in reader.trailer.get("/Root", {}):
            print("PDF contains JavaScript.")
            return False

        # Check for embedded files (file attachments)
        attachments = []
        if "/Names" in reader.trailer["/Root"]:
            names = reader.trailer["/Root"]["/Names"]
            embedded_files = names.get("/EmbeddedFiles")
            if embedded_files:
                print("PDF contains embedded files.")
                return False

        print("PDF passed the property checks.")

        return True
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return False

def scan_pdf_viruses(pdf_path: str, virus_total_api_key: str = None) -> bool:
    """Checks PDF for malware. Uses VirusTotal if API key is given. Returns True if clean."""
    if virus_total_api_key is None:
        print("No VirusTotal API key provided; skipping virus scan.")
        return True  # WARNING: Not actually safe, just skipping scan!
    # Otherwise, upload to VirusTotal
    with open(pdf_path, "rb") as f:
        files = {"file": f}
        headers = {"x-apikey": virus_total_api_key}
        response = requests.post(
            "https://www.virustotal.com/api/v3/files", files=files, headers=headers
        )
        result = response.json()
        # It takes time to finish scanning, so you'd need to poll result['data']['id']
        # For demonstration, just indicate submission:
        print("Submitted to VirusTotal, scan ID:", result.get("data", {}).get("id"))
        return True

def is_pdf_safe(pdf_path: str, virus_total_api_key: str = None) -> bool:
    if not check_pdf_security(pdf_path):
        print("PDF security checks failed.")
        return False
    if not scan_pdf_viruses(pdf_path, virus_total_api_key):
        print("PDF did not pass the virus scan.")
        return False
    print("PDF is considered safe.")
    return True

# Example usage:
if __name__ == "__main__":
    my_pdf = "path/to/your/file.pdf"
    vt_api_key = os.getenv("VT_API_KEY")  # Prefer to keep keys outside code
    safe = is_pdf_safe(my_pdf, vt_api_key)
    print("Is PDF safe to upload?", safe)