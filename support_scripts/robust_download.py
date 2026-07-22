import urllib.request
import json
import subprocess
import os

packages = {
    "torch": "2.13.0",
    "nvidia-cufft": "12.0.0.61",
    "nvidia-cusolver": "12.0.4.66",
    "nvidia-cublas": "13.1.1.3",
    "nvidia-cudnn-cu13": "9.20.0.48",
    "nvidia-cusparse": "12.6.3.3",
    "nvidia-cusparselt-cu13": "0.8.1",
    "nvidia-cuda-nvrtc": "13.0.88",
    "nvidia-nccl-cu13": "2.21.5",
}

os.makedirs("wheels", exist_ok=True)

for pkg, version in packages.items():
    print(f"Fetching URLs for {pkg}=={version}")
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/{version}/json") as f:
            data = json.loads(f.read().decode("utf-8"))
        
        # Try to find a cp314 or py3 wheel for x86_64
        for u in data["urls"]:
            fname = u["filename"]
            if "x86_64" in fname and ("cp314" in fname or "py3" in fname):
                print(f"Downloading {fname} with wget -c")
                subprocess.run(["wget", "-c", "--secure-protocol=TLSv1_2", "--retry-connrefused", "--read-timeout=15", "--timeout=15", "-t", "50", "-O", f"wheels/{fname}", u["url"]])
                break
    except Exception as e:
        print(f"Failed on {pkg}: {e}")
