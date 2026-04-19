# patch_collectors_async.py
import os

PROJECT_PATH = r"C:\Users\matam\Projects\FORGE\forage\collectors"
COLLECTORS = [
    "gdelt_collector.py",
    "civic_intel_collector.py",
    "firms_collector.py",
    "rss_collector.py",
    "earthquake_collector.py",
    "usgs_collector.py"
]

async_template = """
# Added by async patch for mega_ingest.py
import asyncio

async def async_main(**kwargs):
    try:
        # Use existing main() if available but avoid nested asyncio.run
        if 'main' in globals():
            result = main(**kwargs)
            if asyncio.iscoroutine(result):
                await result
        else:
            print("[WARN] No main() function found in {filename}")
    except Exception as e:
        print(f"[ERROR] async_main failed in {filename}: {{e}}")
"""

for collector_file in COLLECTORS:
    path = os.path.join(PROJECT_PATH, collector_file)
    if not os.path.exists(path):
        print(f"[SKIP] {collector_file} does not exist")
        continue

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # Skip if async_main already exists
    if "async_main" in content:
        print(f"[SKIP] async_main already present in {collector_file}")
        continue

    # Append async_main template
    appended_content = content + async_template.format(filename=collector_file)
    with open(path, "w", encoding="utf-8") as f:
        f.write(appended_content)

    print(f"[PATCHED] {collector_file} with async_main()")