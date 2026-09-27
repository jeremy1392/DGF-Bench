#!/usr/bin/env python3
from pathlib import Path
import argparse, os, json
from dgf_bench.azure_icon_registry import install_from_zip, download_official, AzureIconRegistry, OFFICIAL_URL

def main():
    ap=argparse.ArgumentParser(description="Install Microsoft official Azure Architecture SVG icon pack for DGF-Bench.")
    ap.add_argument("--zip",type=Path,help="Path to Azure_Public_Service_Icons_V24.zip")
    ap.add_argument("--download",action="store_true",help="Download the official pack from Microsoft (requires internet).")
    ap.add_argument("--assets",type=Path,default=Path.home()/".dgf-bench"/"assets",help="Install directory (default: ~/.dgf-bench/assets); the packaged icons stay unchanged")
    args=ap.parse_args()
    zp=args.zip
    if args.download:
        zp=args.assets/"Azure_Public_Service_Icons_V24.zip"; args.assets.mkdir(parents=True,exist_ok=True)
        print("Downloading",OFFICIAL_URL); download_official(zp)
    if not zp or not zp.exists(): raise SystemExit("Provide --zip /path/Azure_Public_Service_Icons_V24.zip or use --download")
    root=install_from_zip(zp,args.assets)
    print("Official Azure icons installed at:",root)
    print("Set DGF_AZURE_ICON_ROOT=",root)
if __name__=="__main__": main()
