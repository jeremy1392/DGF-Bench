#!/usr/bin/env python3
from __future__ import annotations
import base64, os, re, zipfile, shutil, urllib.request
from pathlib import Path
from difflib import SequenceMatcher
from typing import Optional

OFFICIAL_URL = "https://arch-center.azureedge.net/icons/Azure_Public_Service_Icons_V24.zip"

ALIASES = {
    "azure front door premium": ["front door and cdn profiles", "front door"],
    "azure front door": ["front door and cdn profiles", "front door"],
    "azure cdn": ["front door and cdn profiles", "cdn profiles", "cdn"],
    "waf + cdn": ["front door and cdn profiles", "web application firewall"],
    "application gateway waf": ["application gateways", "application gateway"],
    "azure application gateway": ["application gateways", "application gateway"],
    "azure firewall": ["firewalls", "azure firewall"],
    "azure bastion": ["bastions", "bastion"],
    "private dns": ["dns private resolver", "dns zones", "private dns"],
    "nat gateway": ["nat gateways", "nat gateway"],
    "er / vpn gateway": ["expressroute circuits", "vpn gateways", "expressroute"],
    "expressroute": ["expressroute circuits", "expressroute"],
    "vpn gateway": ["vpn gateways", "vpn gateway"],
    "azure api management": ["api management services", "api management"],
    "azure vm": ["virtual machine", "virtual machines"],
    "azure vm az1": ["virtual machine", "virtual machines"],
    "azure vm az2": ["virtual machine", "virtual machines"],
    "vm group a": ["virtual machine", "virtual machines"],
    "vm group b": ["virtual machine", "virtual machines"],
    "vm scale set az1": ["vm scale sets", "virtual machine scale sets"],
    "vm scale set az2": ["vm scale sets", "virtual machine scale sets"],
    "vm scale set az3": ["vm scale sets", "virtual machine scale sets"],
    "azure dedicated host": ["host groups", "dedicated hosts", "hosts"],
    "aks system pool": ["kubernetes services", "kubernetes service"],
    "aks app pool": ["kubernetes services", "kubernetes service"],
    "ingress controller": ["kubernetes services", "application gateway for containers"],
    "worker pods": ["kubernetes services", "kubernetes service"],
    "azure container apps environment": ["container apps", "container apps environments"],
    "frontend container": ["container apps", "container instances"],
    "api container": ["container apps", "container instances"],
    "worker container": ["container apps", "container instances"],
    "azure app service": ["app services", "app service"],
    "deployment slot": ["app services", "app service"],
    "azure functions": ["function apps", "functions"],
    "function app workers": ["function apps", "functions"],
    "batch vm": ["virtual machine", "batch accounts"],
    "azure sql database": ["sql databases", "sql database"],
    "azure sql managed instance": ["sql managed instance", "sql managed instances"],
    "azure database for postgresql": ["azure database for postgresql", "postgresql flexible server"],
    "azure cosmos db": ["azure cosmos db", "cosmos db"],
    "azure storage account": ["storage accounts", "storage account"],
    "azure data lake storage": ["data lake storage gen1", "storage accounts"],
    "azure cache for redis": ["cache for redis", "managed redis"],
    "azure managed redis": ["managed redis", "cache for redis"],
    "azure key vault": ["key vaults", "key vault"],
    "azure service bus": ["service bus", "service bus namespaces"],
    "azure event hubs": ["event hubs", "event hub clusters"],
    "azure ai search": ["search services", "cognitive search"],
    "azure synapse / fabric endpoint": ["azure synapse analytics", "synapse analytics"],
    "azure container registry": ["container registries", "container registry"],
    "azure monitor": ["monitor", "azure monitor"],
    "log analytics": ["log analytics workspaces", "log analytics"],
    "log analytics / sentinel": ["sentinel", "log analytics workspaces"],
    "microsoft sentinel": ["sentinel", "microsoft sentinel"],
    "defender for cloud": ["defender for cloud", "security center"],
    "application insights": ["application insights", "app insights"],
    "azure backup / rsv": ["recovery services vaults", "backup center", "azure backup"],
    "recovery vault": ["recovery services vaults", "recovery services vault"],
    "azure site recovery": ["recovery services vaults", "site recovery"],
    "microsoft entra id": ["microsoft entra id", "azure active directory", "entra id"],
    "hub vnet": ["virtual networks", "virtual network"],
    "primary region": ["azure", "regions"],
    "secondary region": ["azure", "regions"],
    "azure virtual wan": ["virtual wans", "virtual wan"],
    "azure data factory": ["data factories", "data factory"],
    "azure ai foundry": ["ai foundry", "machine learning", "cognitive services"],
    "azure openai service": ["azure openai", "cognitive services", "ai services"],
    "internet users": ["users", "web"],
    "corporate users": ["users", "active directory"],
    "corporate network": ["virtual networks", "network watcher"],
}

STOP = {"azure","microsoft","service","services","app","apps","and","for","the","premium","environment","az1","az2","az3","primary","secondary"}

def norm(s:str)->str:
    s=s.lower().replace("&"," and ").replace("/"," ")
    s=re.sub(r"[^a-z0-9]+"," ",s)
    toks=[t for t in s.split() if t not in STOP]
    return " ".join(toks)

class AzureIconRegistry:
    def __init__(self, root:Path|str|None=None):
        env=os.environ.get("DGF_AZURE_ICON_ROOT")
        base=Path(__file__).resolve().parent
        default_root = base/"assets"/"azure-icons"/"Azure_Public_Service_Icons"/"Icons"
        pointer = base/"assets"/"azure_icon_root.txt"
        if root or env:
            resolved=Path(root or env)
        elif default_root.exists():
            resolved=default_root
        elif pointer.exists():
            raw=pointer.read_text(encoding="utf-8").strip()
            cand=Path(raw)
            if not cand.is_absolute():
                cand=(base/"assets"/raw).resolve()
            resolved=cand
        else:
            resolved=default_root
        self.root=resolved
        self.files=[]
        self.index=[]
        if self.root.exists():
            self.files=sorted(self.root.rglob("*.svg"))
            for p in self.files:
                self.index.append((p,norm(p.stem),norm(" ".join(p.parts[-3:]))))

    @property
    def available(self): return bool(self.files)

    def resolve(self, service_name:str)->Optional[Path]:
        if not self.files: return None
        key=service_name.lower().strip()
        candidates=ALIASES.get(key,[service_name])
        best=None; bestscore=0.0
        for query in candidates:
            nq=norm(query)
            for p,stem,full in self.index:
                # exact token/substring strongly preferred
                if nq and (nq==stem or nq in stem or stem in nq): score=.98
                elif nq and nq in full: score=.94
                else:
                    score=max(SequenceMatcher(None,nq,stem).ratio(), SequenceMatcher(None,nq,full).ratio()*.92)
                if score>bestscore:
                    bestscore=score; best=p
        return best if bestscore>=.46 else None

    def data_uri(self, service_name:str)->Optional[str]:
        p=self.resolve(service_name)
        if not p: return None
        raw=p.read_bytes()
        return "data:image/svg+xml;base64,"+base64.b64encode(raw).decode("ascii")

    def mapping_report(self, names):
        out=[]
        for n in names:
            p=self.resolve(n)
            out.append({"service":n,"icon":str(p.relative_to(self.root)) if p else None})
        return out

def safe_extract(zip_path:Path, dest:Path):
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target=(dest/info.filename).resolve()
            if dest.resolve() not in target.parents or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unsafe ZIP path")
        z.extractall(dest)

def locate_icons_root(dest:Path)->Path:
    # official archive normally contains Azure_Public_Service_Icons/Icons/
    cands=[]
    for p in dest.rglob("Icons"):
        if p.is_dir() and len(list(p.rglob("*.svg")))>50:
            cands.append(p)
    if not cands:
        # accept any extracted root with many SVGs
        if len(list(dest.rglob("*.svg")))>50: return dest
        raise FileNotFoundError("No Azure SVG icon tree found after extraction")
    return max(cands,key=lambda p:len(list(p.rglob("*.svg"))))

def install_from_zip(zip_path:Path, assets_root:Path)->Path:
    import hashlib, tempfile
    assets_root=Path(assets_root).resolve(); assets_root.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(Path(zip_path).read_bytes()).hexdigest()[:16]
    target=assets_root/('azure-icons-'+digest)
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix='.icon-install-',dir=assets_root) as temporary:
            staging=Path(temporary)/'icons'; staging.mkdir()
            safe_extract(zip_path,staging)
            locate_icons_root(staging)  # validate before changing the active pointer
            staging.rename(target)
    icons=locate_icons_root(target)
    pointer=assets_root/'azure_icon_root.txt'
    temporary=assets_root/'azure_icon_root.tmp'
    temporary.write_text(str(icons.relative_to(assets_root)),encoding='utf-8')
    temporary.replace(pointer)
    return icons


def download_official(dest_zip:Path):
    # Convenience for users running locally with normal internet access.
    urllib.request.urlretrieve(OFFICIAL_URL,dest_zip)
    return dest_zip
