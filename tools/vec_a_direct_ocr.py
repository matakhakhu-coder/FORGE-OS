from pathlib import Path
import os
import requests
import sqlite3
import hashlib
import subprocess
from datetime import datetime

# TARGETS: A1-Grade DOJ Manifests
TARGETS = [
    ('https://www.justice.gov/opa/press-release/file/1453306/download', 'DOJ_Epstein_Manifest_Alpha.pdf'),
    ('https://www.justice.gov/opa/press-release/file/1453311/download', 'DOJ_Epstein_Manifest_Beta.pdf')
]

DOC_PATH = 'media/documents'
DB_PATH = str(Path(__file__).resolve().parent.parent / "database.db")

# OPSEC: Mimic a legitimate browser to bypass DOJ anti-bot firewalls
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Referer': 'https://www.justice.gov/'
}

def direct_infiltration():
    print("[OPERATOR] Initiating PRO-MODE Direct VEC-A Infiltration...")
    os.makedirs(DOC_PATH, exist_ok=True)

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
    except sqlite3.Error as e:
        print(f"[-] FATAL DB ERROR: {e}")
        return

    for url, filename in TARGETS:
        local_file = os.path.join(DOC_PATH, filename)
        print(f"[+] Targeting: {filename}...")

        try:
            # STREAMING DOWNLOAD: Safe for massive PDF files
            with requests.get(url, headers=HEADERS, stream=True, timeout=45) as r:
                r.raise_for_status()
                with open(local_file, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
            print(f"    [SUCCESS] Payload secured at {local_file}")

            # DETERMINISTIC ID: Prevents database ghost duplicates
            art_id = hashlib.md5(url.encode()).hexdigest()

            # ATOMIC INSERT: Only inject if it doesn't already exist
            cursor.execute("""
                INSERT INTO artifacts (artifact_id, title, file_path, status, created_at)
                SELECT ?, ?, ?, 'pending_ocr', ?
                WHERE NOT EXISTS (SELECT 1 FROM artifacts WHERE artifact_id = ?)
            """, (art_id, filename, local_file, datetime.now().isoformat(), art_id))

        except requests.exceptions.RequestException as e:
            print(f"    [-] NETWORK FAILURE (DOJ Firewall/Timeout): {e}")
        except sqlite3.Error as e:
            print(f"    [-] DATABASE FAULT: {e}")

    # Commit all changes atomically
    conn.commit()
    conn.close()

    print("\n[OPERATOR] Manifests staged. Igniting P2-04 OCR Bridge...")

    # SUBPROCESS CALL: Secure execution of the secondary script
    try:
        subprocess.run(["python", "forage/collectors/pdf_infiltrator.py", "--reprocess-vault"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"[-] SUBPROCESS HALT: OCR Bridge failed with code {e.returncode}")

if __name__ == "__main__":
    direct_infiltration()