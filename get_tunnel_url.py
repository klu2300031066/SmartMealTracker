import subprocess
import time
import os

print("Starting cloudflared tunnel...")
process = subprocess.Popen(
    ['./cloudflared.exe', 'tunnel', '--url', 'http://localhost:8000'],
    stderr=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

start_time = time.time()
found = False

while time.time() - start_time < 30:
    line = process.stderr.readline()
    if not line:
        break
    print(line.strip())
    if "https://" in line and "trycloudflare.com" in line:
        print("\n" + "="*50)
        print("FOUND URL: " + line.strip())
        print("="*50 + "\n")
        found = True
        break

if not found:
    print("URL not found in stderr within 30 seconds.")
