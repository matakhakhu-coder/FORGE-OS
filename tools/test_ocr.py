import pytesseract
from PIL import Image
import sys

# TELL PYTHON WHERE THE ENGINE IS
# If you didn't add it to PATH manually, this line is mandatory:
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

try:
    version = pytesseract.get_tesseract_version()
    print(f"✅ Success! Tesseract Version: {version}")
    print("✅ Python can now 'see' the OCR engine.")
except Exception as e:
    print(f"❌ Error: {e}")
    print("Check if the path C:\\Program Files\\Tesseract-OCR\\tesseract.exe is correct.")